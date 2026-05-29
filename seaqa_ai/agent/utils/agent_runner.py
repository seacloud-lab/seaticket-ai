"""
AgentRunner: generic ReAct-style LLM function-calling loop.

Works for any source type (ticket, github_issue, discourse_topic, email_thread).
The caller injects a record_context dict that identifies the source record being processed:
  {
    'source_type': 'github_issue',
    'source_id': '3_42',          # '{connection_id}_{record_id}' for non-ticket sources
    'source_title': 'Login fails on Safari',
  }

Actions are recorded to SeaDB via the action_recorder callback.
"""
import json
import logging
import time
from dataclasses import dataclass
from seaqa_ai.utils.llm_api import CompletionAPI
from seaqa_ai.agent.prompts._forced import (
    FORCED_RECORD_EVENT_SUMMARY_PROMPT,
    PHASE_GUIDANCE_FOR_THINK,
    FORCED_RECORD_ANALYSIS_PROMPT,
    FORCED_FINAL_ANSWER_PROMPT,
)
from seaqa_ai.agent.tools.common_tools import FinalAnswerTool, ThinkTool, RecordEventSummaryTool
from seaqa_ai.utils.tools import OpenAIToolExecutor
from litellm import Message, ChatCompletionMessageToolCall
from seaqa_ai.agent.utils import render_task_xml
from seaqa_ai.utils import parse_json, remove_sources_content_and_snippets, get_answer_and_sources

@dataclass(frozen=True)
class PhaseSpec:
    """Describes all invariants that govern one reasoning phase.

    Attributes:
        name: Human-readable label ('analysis' or 'handling').
        terminator_tool: Name of the tool whose invocation ends the phase.
        forced_prompt: System message injected when max_steps is reached
            to force an immediate terminator call.
        max_steps: Maximum number of LLM steps before forced termination.
    """
    name: str
    terminator_tool: str
    forced_prompt: str
    max_steps: int = 10

logger = logging.getLogger(__name__)


class AgentRunner:
    """
    Generic agent that drives a tool-calling reasoning loop.

    Features:
    - Injects record context (source_type, source_id, source_title) into tool calls
    - Records each tool execution as an action in SeaDB via action_recorder
    - Supports 'pending' status for actions requiring user confirmation
    - Phase behaviour (terminator tool, forced prompt, max steps) is expressed as
      PhaseSpec objects so both phases are configured symmetrically
    """

    PRELUDE_TOOL = 'record_event_summary'

    def __init__(
        self,
        tool_executor: OpenAIToolExecutor,
        llm_client: CompletionAPI,
        record_context: dict,
        ai_stats_context: dict | None = None,
        action_recorder=None,
        run_id=None,
        tool_metadata=None,
        analysis_system_prompt=None,
        handling_system_prompt=None,
        analysis_phase: PhaseSpec | None = None,
        handling_phase: PhaseSpec | None = None,
        search_enabled: bool = True
    ):
        """
        Args:
            tool_executor: OpenAIToolExecutor with all tools already registered.
            llm_client: CompletionAPI client used for LLM calls.
            record_context: dict with keys source_type, source_id, source_title.
            ai_stats_context: dict with keys used for AI usage statistics logging
                            (e.g. org_id, project_uuid).
            action_recorder: callable(run_id, source_type, source_id, source_title,
                             action_type, tool_name, content, result, action_status,
                             suggestion_text, sources, statistics) -> int
            run_id: current agent run _pk in SeaDB.
            tool_metadata: dict mapping tool_name to {action_type, action_status, suggestion_text}.
            analysis_system_prompt: system message for the analysis phase.
            handling_system_prompt: system message for the handling phase.
            analysis_phase: PhaseSpec describing the analysis phase (terminator tool,
                forced prompt, max steps).  Defaults to record_issue_analysis / 10 steps.
            handling_phase: PhaseSpec describing the handling phase (terminator tool,
                forced prompt, max steps).  Defaults to final_answer / 5 steps.
            search_enabled: when False, *_search tools are hidden from the analysis phase.
        """
        self.tool_executor = tool_executor
        self.llm_client = llm_client
        self.record_context = record_context
        self.ai_stats_context = ai_stats_context or {}
        self.action_recorder = action_recorder
        self.run_id = run_id
        self.tool_metadata = tool_metadata or {}
        self.analysis_system_prompt = analysis_system_prompt
        self.handling_system_prompt = handling_system_prompt
        self.analysis_phase = analysis_phase or PhaseSpec(
            name='analysis',
            terminator_tool='record_issue_analysis',
            forced_prompt=FORCED_RECORD_ANALYSIS_PROMPT,
        )
        self.handling_phase = handling_phase or PhaseSpec(
            name='handling',
            terminator_tool='final_answer',
            forced_prompt=FORCED_FINAL_ANSWER_PROMPT,
        )
        self.search_enabled = search_enabled
        self.event_summary = ''

        final_answer_tool = FinalAnswerTool()
        final_answer_tool.register(tool_executor)

        record_event_summary_tool = RecordEventSummaryTool()
        record_event_summary_tool.set_record_context(self.record_context)
        record_event_summary_tool.register(tool_executor)
        self.tool_metadata[record_event_summary_tool.tool.get('function', {}).get('name')] = {
            'action_type': record_event_summary_tool.action_type,
            'action_status': record_event_summary_tool.action_status,
        }

        think_tool = ThinkTool()
        think_tool.register(tool_executor)
        self.tool_metadata[think_tool.tool.get('function', {}).get('name')] = {
            'action_type': think_tool.action_type,
            'action_status': think_tool.action_status,
        }

    def run(self, task: dict) -> None:
        """Drive the two-phase reasoning loop: analysis then handling.

        Args:
            task: structured task payload (source_id, event, record, ...).
        """

        self.event_summary = ''
        analysis_task_xml = render_task_xml(task)
        messages = self._build_initial_messages(analysis_task_xml)

        self._record_event_summary(messages)
        self._run_phase(messages, self.analysis_phase)
        handling_messages = self._build_handling_messages(task, messages)
        self._run_phase(handling_messages, self.handling_phase)

    def _run_phase(self, messages: list[dict], spec: PhaseSpec) -> None:
        """Run a single reasoning phase until the terminator tool is called or max steps reached."""
        for step in range(spec.max_steps):
            # self._force_think_step(messages, step, phase=spec.name)
            step_start = time.monotonic()
            message, usage = self._request_llm_message_for_phase(messages, step, phase=spec.name)
            if message is None:
                logger.warning(f'{spec.name} step {step} LLM call failed, continuing to next step.')
                continue

            tool_call = self._validate_single_tool_call(message.tool_calls, step)
            assistant_message = self._build_assistant_message(message, tool_call=tool_call)
            messages.append(assistant_message)

            if not tool_call:
                logger.warning(f'{spec.name} step {step} returned no tool call.')
                continue

            tool_name = tool_call.function.name
            logger.debug(f'Processing tool call {tool_name} at step {step}')
            self._process_tool_call(tool_call, messages, step_start=step_start, usage=usage, phase=spec.name, step=step)

            if tool_name == spec.terminator_tool:
                logger.debug(f'{spec.name} loop completed at step {step} via {tool_name}.')
                return

        logger.warning(
            f'{spec.name} loop reached max steps ({spec.max_steps}) '
            f'without calling {spec.terminator_tool!r}.'
        )
        self._force_terminator(messages, spec)

    def _build_handling_messages(self, task: dict, analysis_messages: list[dict]) -> list[dict]:
        event_summary = self.event_summary
        search_results = self._extract_successful_search_results(analysis_messages)
        analysis_result = self._extract_analysis_result(analysis_messages)

        handling_task_content = self._build_handling_task_content(
            original_task=task,
            event_summary=event_summary,
            search_results=search_results,
            analysis_result=analysis_result,
        )

        return [
            {'role': 'system', 'content': self.handling_system_prompt},
            {'role': 'user', 'content': handling_task_content},
        ]

    def _extract_successful_search_results(self, messages: list[dict]) -> list[dict]:
        search_results = []

        for i, msg in enumerate(messages):
            if msg.get('role') != 'assistant' or not msg.get('tool_calls'):
                continue

            for tool_call in msg['tool_calls']:
                tool_name = tool_call.get('function', {}).get('name', '')
                if not tool_name.endswith('_search'):
                    continue

                tool_call_id = tool_call.get('id')
                for next_msg in messages[i + 1:]:
                    if (next_msg.get('role') == 'tool' and
                            next_msg.get('tool_call_id') == tool_call_id):
                        content = next_msg.get('content', '')
                        if not self._is_search_failed(content):
                            search_results.append({
                                'tool_name': tool_name,
                                'content': content,
                            })
                        break

        return search_results

    def _is_search_failed(self, content: str) -> bool:
        try:
            result = json.loads(content)
            if isinstance(result, dict):
                return result.get('status') == 'error'
        except (json.JSONDecodeError, TypeError):
            pass
        return False

    def _extract_analysis_result(self, messages: list[dict]) -> dict:
        for msg in reversed(messages):
            if msg.get('role') != 'assistant' or not msg.get('tool_calls'):
                continue

            for tool_call in msg['tool_calls']:
                tool_name = tool_call.get('function', {}).get('name', '')
                if tool_name == self.analysis_phase.terminator_tool:
                    args_str = tool_call.get('function', {}).get('arguments', '{}')
                    return self._parse_tool_arguments(args_str)
        return {}

    def _build_handling_task_content(self, original_task: dict, event_summary: str, search_results: list[dict], analysis_result: dict) -> str:
        task_dict = dict(original_task)

        if event_summary:
            task_dict['event_summary'] = event_summary

        if search_results:
            task_dict['search_results'] = [
                {
                    'tool_name': sr['tool_name'],
                    'content': parse_json(sr['content']),
                }
                for sr in search_results
            ]

        if analysis_result:
            task_dict['analysis_summary'] = analysis_result

        return render_task_xml(task_dict)


    def _request_llm_message_for_phase(
        self,
        messages: list[dict],
        step: int,
        phase: str,
        use_tools: bool = True,
    ) -> tuple[Message | None, dict]:
        """Request an LLM message with phase-specific tools."""
        total_chars = 0
        for i, m in enumerate(messages):
            content_len = len(str(m.get('content') or ''))
            logger.debug('[AgentRunner] step=%d msg[%d] role=%s content_chars=%d', step, i, m.get('role'), content_len)
            total_chars += content_len
        logger.debug('[AgentRunner] step=%d len(messages)=%d total_chars=%d (~%d tokens)', step, len(messages), total_chars, total_chars // 4)

        request_kwargs = {
            'messages': messages,
            'context': self.ai_stats_context,
        }
        if use_tools:
            if phase == 'analysis':
                tools = self._get_analysis_tools()
            else:
                tools = self._get_handling_tools()

            request_kwargs.update({
                'tools': tools,
                'tool_choice': 'auto',
            })

        llm_start = time.monotonic()
        try:
            response = self.llm_client.completion(**request_kwargs)
            return response.choices[0].message, self._extract_usage(response)
        except Exception as e:
            duration_ms = int((time.monotonic() - llm_start) * 1000)
            logger.exception(f'LLM call failed at {phase} step {step}: {e}')
            if self.action_recorder and self.run_id:
                self._record_action(
                    phase=phase,
                    result=str(e),
                    action_type='error',
                    action_status='failed',
                    step=step,
                    statistics=json.dumps({'duration_ms': duration_ms})
                )
            return None, {}

    def _build_initial_messages(self, task: str) -> list[dict]:
        return [
            {'role': 'system', 'content': self.analysis_system_prompt},
            {'role': 'user', 'content': task},
        ]

    def _force_think_step(self, messages: list[dict], step: int, phase: str):
        source_type = self.record_context.get('source_type', '')
        if phase == 'analysis' and source_type == 'ticket':
            guidance_key = 'ticket_analysis'
        elif phase == 'analysis' and source_type != 'ticket':
            guidance_key ='issue_analysis'
        else:
            guidance_key = phase
        self._force_single_tool_call(
            messages=messages,
            tool_name='think',
            step=step,
            phase=phase,
            system_content=PHASE_GUIDANCE_FOR_THINK.get(guidance_key),
        )

    def _record_event_summary(self, messages: list[dict]) -> None:
        args = self._force_single_tool_call(
            messages=messages,
            tool_name=self.PRELUDE_TOOL,
            step=0,
            phase='prelude',
            system_content=FORCED_RECORD_EVENT_SUMMARY_PROMPT,
        )
        self.event_summary = (args.get('summary') or '').strip()

    def _force_terminator(self, messages: list[dict], spec: PhaseSpec) -> None:
        """Force-call the terminator tool for a phase when max steps is exceeded."""
        self._force_single_tool_call(
            messages=messages,
            tool_name=spec.terminator_tool,
            step=spec.max_steps,
            phase=spec.name,
            system_content=spec.forced_prompt,
        )

    def _force_single_tool_call(
        self,
        messages: list[dict],
        tool_name: str,
        step: int,
        phase: str,
        system_content: str,
    ) -> dict:
        tool = self._get_registered_tool_by_name(tool_name)
        if not tool:
            logger.warning(f'{tool_name} tool is not registered; skipping forced tool call.')
            return {}

        forced_messages = messages + [{
            'role': 'system',
            'content': system_content,
        }]

        completion_kwargs = {
            'messages': forced_messages,
            'context': self.ai_stats_context,
            'tools': [tool],
            'tool_choice': {
                'type': 'function',
                'function': {
                    'name': tool_name,
                },
            },
        }
        step_start = time.monotonic()
        try:
            response = self.llm_client.completion(**completion_kwargs)
            message = response.choices[0].message
            usage = self._extract_usage(response)
        except Exception as e:
            duration_ms = int((time.monotonic() - step_start) * 1000)
            logger.exception(f'Forced {tool_name} call failed at step {step}: {e}')
            if self.action_recorder and self.run_id:
                self._record_action(
                    phase=phase,
                    result=str(e),
                    action_type='error',
                    action_status='failed',
                    step=step,
                    tool_name=tool_name,
                    forced_tool_call=True,
                    statistics=json.dumps({'duration_ms': duration_ms})
                )
            return {}

        tool_call = self._validate_single_tool_call(message.tool_calls, step)
        assistant_message = self._build_assistant_message(message, tool_call=tool_call)
        messages.append(assistant_message)

        if not tool_call:
            logger.warning(f'Forced {tool_name} step {step} returned no tool call.')
            if self.action_recorder and self.run_id:
                duration_ms = int((time.monotonic() - step_start) * 1000)
                self._record_action(
                    phase=phase,
                    result="Forced tool call did not produce a tool call.",
                    action_type='error',
                    action_status='failed',
                    step=step,
                    tool_name=tool_name,
                    forced_tool_call=True,
                    statistics=json.dumps({'duration_ms': duration_ms, **usage})
                )
            return {}

        arguments = self._parse_tool_arguments(tool_call.function.arguments)
        self._process_tool_call(tool_call, messages, step_start=step_start, usage=usage, phase=phase, step=step, forced=True)
        return arguments

    @staticmethod
    def _parse_tool_arguments(raw_arguments) -> dict:
        try:
            parsed_arguments = json.loads(raw_arguments) if isinstance(raw_arguments, str) else (raw_arguments or {})
        except (json.JSONDecodeError, TypeError):
            return {}
        return parsed_arguments if isinstance(parsed_arguments, dict) else {}

    def _get_analysis_tools(self) -> list[dict]:
        excluded_prefixes = ('suggest_',)
        excluded_names = (self.PRELUDE_TOOL, self.handling_phase.terminator_tool, 'think')
        tools = [
            tool
            for tool in self.tool_executor.get()
            if not tool.get('function', {}).get('name', '').startswith(excluded_prefixes)
            and tool.get('function', {}).get('name') not in excluded_names
        ]
        if not self.search_enabled:
            tools = [
                tool for tool in tools
                if not tool.get('function', {}).get('name', '').endswith('_search')
            ]
        return tools

    def _get_handling_tools(self) -> list[dict]:
        excluded_suffixes = ('_search',)
        excluded_names = {self.analysis_phase.terminator_tool, self.PRELUDE_TOOL, 'think'}
        return [
            tool
            for tool in self.tool_executor.get()
            if not tool.get('function', {}).get('name', '').endswith(excluded_suffixes)
            and tool.get('function', {}).get('name') not in excluded_names
        ]

    def _validate_single_tool_call(
        self,
        tool_calls: list[ChatCompletionMessageToolCall] | None,
        step: int,
    ) -> ChatCompletionMessageToolCall | None:
        """Keep only one tool call per step and warn when model returns more."""
        if not tool_calls:
            return None
        if len(tool_calls) == 1:
            return tool_calls[0]

        tool_names = [tc.function.name for tc in tool_calls]
        logger.warning(
            'Agent step %s returned %s tool calls; keeping only the first one. tool_names=%s',
            step,
            len(tool_calls),
            tool_names,
        )
        return tool_calls[0]

    def _get_registered_tool_by_name(self, tool_name: str) -> dict | None:
        for tool in self.tool_executor.get():
            function = tool.get('function', {})
            if function.get('name') == tool_name:
                return tool
        return None

    def _build_assistant_message(
        self,
        message: Message,
        tool_call: ChatCompletionMessageToolCall | None = None,
    ) -> dict:
        msg_dict = {'role': 'assistant', 'content': message.content or ''}
        if tool_call:
            msg_dict['tool_calls'] = [{
                'id': tool_call.id,
                'type': 'function',
                'function': {
                    'name': tool_call.function.name,
                    'arguments': tool_call.function.arguments,
                }
            }]
        return msg_dict

    @staticmethod
    def _extract_usage(response) -> dict:
        try:
            usage = response.usage
            return {
                'input_tokens': usage.prompt_tokens,
                'output_tokens': usage.completion_tokens,
                'total_tokens': usage.total_tokens,
            }
        except AttributeError:
            return {}

    @staticmethod
    def _build_statistics(
        step_start: float | None,
        usage: dict | None,
        tool_token_usage: dict | None = None,
    ) -> str | None:
        """Build a JSON string with token usage and duration for one step.

        usage: tokens from the LLM completion call.
        tool_token_usage: extra tokens consumed inside tool execution (e.g. rerank).
        """
        stats = {}
        if usage:
            stats.update(usage)
        if tool_token_usage:
            for key in ('input_tokens', 'output_tokens', 'total_tokens'):
                if tool_token_usage.get(key):
                    stats[key] = stats.get(key, 0) + tool_token_usage[key]
        if step_start is not None:
            stats['duration_ms'] = int((time.monotonic() - step_start) * 1000)
        return json.dumps(stats) if stats else None

    def _process_tool_call(
        self,
        tool_call: ChatCompletionMessageToolCall,
        messages: list[dict],
        step_start: float | None = None,
        usage: dict | None = None,
        phase: str | None = None,
        step: int | None = None,
        forced: bool = False
    ) -> str | None:
        tool_name = tool_call.function.name
        is_final_answer = tool_name == 'final_answer'
        tool_failed = False
        usage = usage or {}

        try:
            raw_arguments = tool_call.function.arguments
            parsed_arguments = json.loads(raw_arguments) if isinstance(raw_arguments, str) else (raw_arguments or {})
            if not isinstance(parsed_arguments, dict):
                raise TypeError('Tool arguments must be a JSON object.')
            arguments = parsed_arguments
        except (json.JSONDecodeError, TypeError) as e:
            tool_failed = True
            arguments = {}
            logger.exception(f'Failed to parse tool arguments for {tool_name!r}: {e}')
            observation = json.dumps({
                'status': 'error',
                'tool_name': tool_name,
                'error_type': type(e).__name__,
                'error_message': str(e),
            }, ensure_ascii=False)
            if self.action_recorder and self.run_id:
                duration_ms = int((time.monotonic() - step_start) * 1000)
                self._record_action(
                    phase=phase,
                    result=str(e),
                    action_type='error',
                    action_status='failed',
                    step=step,
                    tool_name=tool_name,
                    tool_arguments=arguments,
                    forced_tool_call=forced,
                    statistics=json.dumps({'duration_ms': duration_ms, **usage})
                )
            logger.debug(f'Observation: {observation}')
            if is_final_answer:
                return observation
            messages.append({
                'role': 'tool',
                'tool_call_id': tool_call.id,
                'content': observation,
            })
            return None

        logger.debug(f"Calling tool: '{tool_name}' with arguments: {arguments}")
        self.tool_executor.thought_process.add_tool_calls_group()
        try:
            result = self.tool_executor.execute(tool_call)
            if result is None:
                observation = ''
            elif isinstance(result, str):
                observation = result.strip()
            elif isinstance(result, (dict, list)):
                observation = json.dumps(result, ensure_ascii=False)
            else:
                observation = str(result).strip()
        except Exception as e:
            tool_failed = True
            logger.exception(f'Tool execution failed for {tool_name!r}: {e}')
            observation = json.dumps({
                'status': 'error',
                'tool_name': tool_name,
                'error_type': type(e).__name__,
                'error_message': str(e),
            }, ensure_ascii=False)
            if self.action_recorder and self.run_id:
                duration_ms = int((time.monotonic() - step_start) * 1000)
                self._record_action(
                    phase=phase,
                    result=str(e),
                    action_type='error',
                    action_status='failed',
                    step=step,
                    tool_name=tool_name,
                    tool_arguments=arguments,
                    forced_tool_call=forced,
                    statistics=json.dumps({'duration_ms': duration_ms, **usage})
                )
        finally:
            tool_token_usage = {}
            if self.tool_executor.thought_process.actions:
                last_group = self.tool_executor.thought_process.actions.pop()
                tool_token_usage = last_group.get('token_usage', {})
        logger.debug(f'Observation: {observation}')

        if not tool_failed:
            statistics = self._build_statistics(step_start, usage, tool_token_usage=tool_token_usage)
            self._record_tool_action(phase, step, tool_name, arguments, observation, forced, statistics=statistics,)

        if is_final_answer:
            return observation

        messages.append({
            'role': 'tool',
            'tool_call_id': tool_call.id,
            'content': observation,
        })

    def _record_tool_action(
        self,
        phase: str,
        step: int,
        tool_name: str,
        tool_arguments: dict,
        observation: str,
        forced: bool,
        statistics: str | None = None
    ):
        if not (self.action_recorder and self.run_id):
            return

        if tool_name == 'final_answer':
            summary = tool_arguments.get('summary') or tool_arguments.get('answer') or observation
            self._record_action(
                phase=phase,
                result=summary,
                action_type='summary',
                action_status='completed',
                step=step,
                tool_name=tool_name,
                tool_arguments=tool_arguments,
                forced_tool_call=forced,
                observation=summary,
                statistics=statistics
            )
            return

        metadata = self.tool_metadata.get(tool_name, {})
        action_type = metadata.get('action_type', 'tool_call')
        action_status = metadata.get('action_status', 'completed')
        if action_type == 'suggestion':
            getter = metadata.get('get_suggestion_text')
            if callable(getter):
                suggestion_text = getter(**tool_arguments)
            else:
                suggestion_text = metadata.get('suggestion_text', '')
        else:
            suggestion_text = ''
        content = (
            tool_arguments.get('thought') or
            tool_arguments.get('analysis') or
            tool_arguments.get('summary') or
            tool_arguments.get('message') or
            tool_arguments.get('comment') or
            tool_arguments.get('reply') or
            tool_arguments.get('reason') or
            tool_arguments.get('rationale') or
            tool_arguments.get('content') or
            ''
        )
        if not content and isinstance(tool_arguments, dict) and tool_arguments:
            content = json.dumps(tool_arguments, ensure_ascii=False)
        sources = []
        if action_type == 'analysis' and content:
            try:
                content, sources = get_answer_and_sources(self.tool_executor, content)
                sources = remove_sources_content_and_snippets(sources)
            except Exception as e:
                logger.warning('Failed to parse analysis references, keep raw content. tool=%s error=%s', tool_name, e)
                sources = []

        result = content
        if action_type == 'suggestion':
            result = suggestion_text
        elif action_type == 'tool_call':
            # use observation for common tool_call
            result = ''

        self._record_action(
            phase=phase,
            result=result,
            action_type=action_type,
            action_status=action_status,
            step=step,
            tool_name=tool_name,
            tool_arguments=tool_arguments,
            forced_tool_call=forced,
            observation=observation,
            suggestion_content=content  if action_type == 'suggestion' else '',
            sources=sources,
            statistics=statistics
        )

    def _record_action(
        self,
        phase,
        result,
        action_type,
        action_status,
        step,
        tool_name=None,
        tool_arguments=None,
        forced_tool_call=False,
        observation=None,
        suggestion_content=None,
        sources=None,
        statistics=None
    ):
        """Record an action to SeaDB via the action_recorder callback."""
        if not self.action_recorder:
            return
        
        if phase == self.analysis_phase.name:
            prompt = self.analysis_system_prompt
            is_max_step = step == self.analysis_phase.max_steps
        elif phase == self.handling_phase.name:
            prompt = self.handling_system_prompt
            self.handling_phase
            is_max_step = step == self.handling_phase.max_steps
        elif action_type == 'events':
            prompt = FORCED_RECORD_EVENT_SUMMARY_PROMPT
            is_max_step = False
        else:
            prompt = ''
            is_max_step = False

        action_data = {
            'run_id': self.run_id,
            'source_type': self.record_context.get('source_type'),
            'source_id': self.record_context.get('source_id'),
            'source_title': self.record_context.get('source_title'),
            'phase': phase,
            'prompt': prompt,
            'result': result,
            'action_type': action_type,
            'action_status': action_status,
            'step': step,
            'is_max_step': is_max_step,
            'tool_name': tool_name,
            'tool_arguments': tool_arguments,
            'forced_tool_call': forced_tool_call,
            'observation': observation or '',
            'suggestion_content': suggestion_content or '',
            'sources': sources or [],
            'statistics': statistics,
        }
            
        try:
            self.action_recorder(**action_data)
        except Exception as e:
            logger.warning(f'Failed to record action: {e}')
