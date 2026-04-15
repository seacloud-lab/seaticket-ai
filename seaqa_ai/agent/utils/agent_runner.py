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
from seaqa_ai.utils.llm_api import CompletionAPI
from seaqa_ai.agent.system_prompt import (
    PHASE_GUIDANCE_FOR_THINK,
    FORCED_RECORD_ISSUE_ANALYSIS_PROMPT,
    FORCED_FINAL_ANSWER_PROMPT,
)
from seaqa_ai.agent.tools.common_tools import FinalAnswerTool, ThinkTool
from seaqa_ai.utils.tools import OpenAIToolExecutor
from litellm import Message, ChatCompletionMessageToolCall
from seaqa_ai.utils import parse_json

logger = logging.getLogger(__name__)


class AgentRunner:
    """
    Generic agent that drives a tool-calling reasoning loop.

    Features:
    - Injects record context (source_type, source_id, source_title) into tool calls
    - Records each tool execution as an action in SeaDB via action_recorder
    - Supports 'pending' status for actions requiring user confirmation
    """

    ANALYSIS_TERMINATORS = ('record_ticket_analysis', 'record_issue_analysis')
    HANDLING_TERMINATOR = 'final_answer'

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
        max_analysis_steps=10,
        max_handling_steps=10,
    ):
        """
        Args:
            tool_executor: OpenAIToolExecutor with all tools already registered.
            llm_client: CompletionAPI client used for LLM calls.
            record_context: dict with keys source_type, source_id, source_title.
            ai_stats_context: dict with keys used for AI usage statistics logging
                            (e.g. username, org_id, project_uuid).
            action_recorder: callable(run_id, source_type, source_id, source_title,
                             action_type, tool_name, content, result, action_status,
                             suggestion_text, statistics) -> int
            run_id: current agent run _pk in SeaDB.
            tool_metadata: dict mapping tool_name to {action_type, action_status, suggestion_text}.
            analysis_system_prompt: system message for the analysis phase.
            handling_system_prompt: system message for the handling phase.
            max_analysis_steps: maximum steps for the analysis loop.
            max_handling_steps: maximum steps for the handling loop.
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
        self.max_analysis_steps = max_analysis_steps
        self.max_handling_steps = max_handling_steps

        final_answer_tool = FinalAnswerTool()
        final_answer_tool.register(tool_executor)

        think_tool = ThinkTool()
        think_tool.register(tool_executor)
        self.tool_metadata[think_tool.tool.get('function', {}).get('name')] = {
            'action_type': think_tool.action_type,
            'action_status': think_tool.action_status,
        }

    def run(self, task: str) -> str:
        """Drive the two-phase reasoning loop: analysis then handling."""
        messages = self._build_initial_messages(task)

        self._run_analysis_loop(messages)
        handling_messages = self._build_handling_messages(task, messages)
        self._run_handling_loop(handling_messages)

    def _run_analysis_loop(self, messages: list[dict]) -> None:
        self._run_loop(
            messages=messages,
            phase='analysis',
            max_steps=self.max_analysis_steps,
            terminators=self.ANALYSIS_TERMINATORS,
            force_fallback=self._force_record_analysis,
        )

    def _run_handling_loop(self, messages: list[dict]) -> None:
        self._run_loop(
            messages=messages,
            phase='handling',
            max_steps=self.max_handling_steps,
            terminators=(self.HANDLING_TERMINATOR,),
            force_fallback=self._force_final_answer,
        )

    def _run_loop(
        self,
        messages: list[dict],
        phase: str,
        max_steps: int,
        terminators: tuple[str, ...],
        force_fallback,
    ) -> None:
        for step in range(max_steps):
            self._force_think_step(messages, step, phase=phase)
            step_start = time.monotonic()
            message, usage = self._request_llm_message_for_phase(messages, step, phase=phase)
            if message is None:
                logger.warning(f'{phase} step {step} LLM call failed, continuing to next step.')
                continue

            tool_call = self._validate_single_tool_call(message.tool_calls, step)
            assistant_message = self._build_assistant_message(message, tool_call=tool_call)
            messages.append(assistant_message)

            if not tool_call:
                logger.warning(f'{phase} step {step} returned no tool call.')
                continue

            tool_name = tool_call.function.name
            logger.debug(f'Processing tool call {tool_name} at step {step}')
            self._process_tool_call(tool_call, messages, step_start=step_start, usage=usage)

            if tool_name in terminators:
                logger.debug(f'{phase} loop completed at step {step} via {tool_name}.')
                return

        logger.warning(
            f'{phase} loop reached max steps ({max_steps}) '
            f'without calling any of {terminators}.'
        )
        force_fallback(messages)

    def _build_handling_messages(self, task: str, analysis_messages: list[dict]) -> list[dict]:
        search_results = self._extract_successful_search_results(analysis_messages)
        analysis_result = self._extract_analysis_result(analysis_messages)

        handling_task_content = self._build_handling_task_content(
            original_task=task,
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
                if tool_name in self.ANALYSIS_TERMINATORS:
                    args_str = tool_call.get('function', {}).get('arguments', '{}')
                    try:
                        return json.loads(args_str) if isinstance(args_str, str) else (args_str or {})
                    except (json.JSONDecodeError, TypeError):
                        return {}
        return {}

    def _build_handling_task_content(self,original_task: str, search_results: list[dict], analysis_result: dict) -> str:
        try:
            task_dict = json.loads(original_task) if isinstance(original_task, str) else {}
            if not isinstance(task_dict, dict):
                task_dict = {'raw': original_task}
        except (json.JSONDecodeError, TypeError):
            task_dict = {'raw': original_task}

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

        return json.dumps(task_dict, ensure_ascii=False, indent=2)


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
                    tool_name=None,
                    arguments={},
                    result=str(e),
                    action_type='error',
                    action_status='failed',
                    content=f'LLM error in {phase} phase: {e}',
                    statistics=json.dumps({'duration_ms': duration_ms}),
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
            system_content=PHASE_GUIDANCE_FOR_THINK.get(guidance_key),
        )

    def _force_record_analysis(self, messages: list[dict]) -> None:
        source_type = self.record_context.get('source_type', '')
        if source_type == 'ticket':
            tool_name = 'record_ticket_analysis'
        else:
            tool_name = 'record_issue_analysis'
        self._force_single_tool_call(
            messages=messages,
            tool_name=tool_name,
            step=self.max_analysis_steps,
            system_content=FORCED_RECORD_ISSUE_ANALYSIS_PROMPT,
        )
    
    def _force_final_answer(self, messages: list[dict]):
        self._force_single_tool_call(
            messages=messages,
            tool_name=self.HANDLING_TERMINATOR,
            step=self.max_handling_steps,
            system_content=FORCED_FINAL_ANSWER_PROMPT,
        )

    def _force_single_tool_call(
        self,
        messages: list[dict],
        tool_name: str,
        step: int,
        system_content: str,
    ):
        tool = self._get_registered_tool_by_name(tool_name)
        if not tool:
            logger.warning(f'{tool_name} tool is not registered; skipping forced tool call.')
            return

        forced_messages = messages + [{
            'role': 'system',
            'content': system_content,
        }]

        step_start = time.monotonic()
        try:
            response = self.llm_client.completion(
                messages=forced_messages,
                context=self.ai_stats_context,
                tools=[tool],
                tool_choice={
                    'type': 'function',
                    'function': {
                        'name': tool_name,
                    },
                },
            )
            message = response.choices[0].message
            usage = self._extract_usage(response)
        except Exception as e:
            duration_ms = int((time.monotonic() - step_start) * 1000)
            logger.exception(f'Forced {tool_name} call failed at step {step}: {e}')
            if self.action_recorder and self.run_id:
                self._record_action(
                    tool_name=tool_name,
                    arguments={},
                    result=str(e),
                    action_type='error',
                    action_status='failed',
                    content=str(e),
                    statistics=json.dumps({'duration_ms': duration_ms}),
                )
            return

        tool_call = self._validate_single_tool_call(message.tool_calls, step)
        assistant_message = self._build_assistant_message(message, tool_call=tool_call)
        messages.append(assistant_message)

        if not tool_call:
            logger.warning(f'Forced {tool_name} step {step} returned no tool call.')
            if self.action_recorder and self.run_id:
                duration_ms = int((time.monotonic() - step_start) * 1000)
                self._record_action(
                    tool_name=tool_name,
                    arguments={},
                    result="No tool call returned.",
                    action_type='error',
                    action_status='failed',
                    content="Forced tool call did not produce a tool call.",
                    statistics=json.dumps({'duration_ms': duration_ms, **usage}),
                )
            return

        self._process_tool_call(tool_call, messages, step_start=step_start, usage=usage)


    def _get_analysis_tools(self) -> list[dict]:
        excluded_prefixes = ('suggest_',)
        excluded_names = (self.HANDLING_TERMINATOR, 'think')
        return [
            tool
            for tool in self.tool_executor.get()
            if not tool.get('function', {}).get('name', '').startswith(excluded_prefixes)
            and tool.get('function', {}).get('name') not in excluded_names
        ]

    def _get_handling_tools(self) -> list[dict]:
        excluded_suffixes = ('_search',)
        excluded_names = self.ANALYSIS_TERMINATORS + ('think',)
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
    ) -> str | None:
        tool_name = tool_call.function.name
        is_final_answer = tool_name == 'final_answer'
        tool_failed = False

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
                    tool_name=tool_name,
                    arguments=arguments,
                    result=str(e),
                    action_type='error',
                    action_status='failed',
                    statistics=json.dumps({'duration_ms': duration_ms, **usage}),
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
                    tool_name=tool_name,
                    arguments=arguments,
                    result=str(e),
                    action_type='error',
                    action_status='failed',
                    statistics=json.dumps({'duration_ms': duration_ms, **usage}),
                )
        finally:
            tool_token_usage = {}
            if self.tool_executor.thought_process.actions:
                last_group = self.tool_executor.thought_process.actions.pop()
                tool_token_usage = last_group.get('token_usage', {})
        logger.debug(f'Observation: {observation}')

        if not tool_failed:
            statistics = self._build_statistics(step_start, usage, tool_token_usage=tool_token_usage)
            self._record_tool_action(tool_name, arguments, observation, is_final_answer, statistics=statistics)

        if is_final_answer:
            return observation

        messages.append({
            'role': 'tool',
            'tool_call_id': tool_call.id,
            'content': observation,
        })

    def _record_tool_action(
        self,
        tool_name: str,
        arguments: dict,
        observation: str,
        is_final_answer: bool,
        statistics: str | None = None,
    ):
        if not (self.action_recorder and self.run_id):
            return

        if is_final_answer:
            summary = arguments.get('summary') or arguments.get('answer') or observation
            self._record_action(
                tool_name='final_answer',
                arguments=arguments,
                result=None,
                action_type='summary',
                action_status='completed',
                content=summary,
                statistics=statistics,
            )
            return

        metadata = self.tool_metadata.get(tool_name, {})
        action_type = metadata.get('action_type', 'tool_call')
        action_status = metadata.get('action_status', 'completed')
        if action_type == 'suggestion':
            getter = metadata.get('get_suggestion_text')
            if callable(getter):
                suggestion_text = getter(**arguments)
            else:
                suggestion_text = metadata.get('suggestion_text', '')
        else:
            suggestion_text = ''
        content = (
            arguments.get('thought') or
            arguments.get('analysis') or
            arguments.get('summary') or
            arguments.get('message') or
            arguments.get('comment') or
            arguments.get('resolution') or
            arguments.get('reason') or
            arguments.get('rationale') or
            arguments.get('content') or
            ''
        )
        if not content and isinstance(arguments, dict) and arguments:
            content = json.dumps(arguments, ensure_ascii=False)
        self._record_action(
            tool_name=tool_name,
            arguments=arguments,
            result=observation,
            action_type=action_type,
            action_status=action_status,
            content=content,
            suggestion_text=suggestion_text,
            statistics=statistics,
        )

    def _record_action(
        self,
        tool_name,
        arguments,
        result,
        action_type,
        action_status,
        content=None,
        suggestion_text=None,
        statistics=None,
    ):
        """Record an action to SeaDB via the action_recorder callback."""
        if not self.action_recorder:
            return
        try:
            self.action_recorder(
                run_id=self.run_id,
                source_type=self.record_context.get('source_type'),
                source_id=self.record_context.get('source_id'),
                source_title=self.record_context.get('source_title'),
                action_type=action_type,
                tool_name=tool_name,
                content=content or '',
                result=result,
                action_status=action_status,
                suggestion_text=suggestion_text or '',
                statistics=statistics,
            )
        except Exception as e:
            logger.exception(f'Failed to record action: {e}')
