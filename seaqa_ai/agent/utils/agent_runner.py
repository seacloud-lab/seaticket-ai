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
from seaqa_ai import config
from seaqa_ai.utils.llm_api import CompletionAPI
from seaqa_ai.utils.tools import OpenAIToolExecutor
from litellm import Message, ChatCompletionMessageToolCall
from seaqa_ai.agent.utils import render_task_xml
from seaqa_ai.agent.utils.phase import Phase, AskPhase, ReactPhase
from seaqa_ai.utils import parse_json, remove_sources_content_and_snippets, get_answer_and_sources
from seaqa_ai.utils.completion import gen_completion_format

logger = logging.getLogger(__name__)


class AgentRetryExceededError(Exception):
    """Raised when agent completion retries are exhausted and the run must stop."""


class AgentRunner:
    """
    Generic agent that drives a prelude step plus a tool-calling reasoning loop.

    Features:
    - Generates an event summary in a dedicated prelude step
    - Injects record context (source_type, source_id, source_title) into tool calls
    - Records each tool execution as an action in SeaDB via action_recorder
    - Supports 'pending' status for actions requiring user confirmation
    - Phase behaviour is expressed as AskPhase / ReactPhase objects
      so analysis and handling remain configured symmetrically
    """

    def __init__(
        self,
        tool_executor: OpenAIToolExecutor,
        llm_client: CompletionAPI,
        record_context: dict,
        ai_stats_context: dict | None = None,
        action_recorder=None,
        run_id=None,
        tool_metadata=None,
        prelude_phase: AskPhase | None = None,
        analysis_phase: ReactPhase | None = None,
        handling_phase: ReactPhase | None = None,
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
            prelude_phase: AskPhase for the prelude event-summary step.
            analysis_phase: ReactPhase for the analysis phase.
            handling_phase: ReactPhase for the handling phase.
        """
        self.tool_executor = tool_executor
        self.llm_client = llm_client
        self.record_context = record_context
        self.ai_stats_context = ai_stats_context or {}
        self.action_recorder = action_recorder
        self.run_id = run_id
        self.tool_metadata = tool_metadata or {}
        self.prelude_phase = prelude_phase
        self.analysis_phase = analysis_phase
        self.handling_phase = handling_phase

    def run(self, task: dict) -> None:
        """Drive the prelude, analysis, and handling flow.

        Args:
            task: structured task payload (source_id, event, record, ...).
        """

        # prelude phase (single-turn Q&A)
        self.prelude_phase.input = render_task_xml(task)
        self._run_phase(self.prelude_phase)

        # analysis phase (multi-step ReAct)
        self._update_analysis_input(task)
        self._run_phase(self.analysis_phase)

        # handling phase (multi-step ReAct)
        self._update_handling_input(task)
        self._run_phase(self.handling_phase)

    def _update_analysis_input(self, task: dict) -> None:
        analysis_task = dict(task)
        prelude_output = self.prelude_phase.output or {}
        if prelude_output.get('summary'):
            analysis_task['event_summary'] = prelude_output['summary']
        self.analysis_phase.input = render_task_xml(analysis_task)

    def _update_handling_input(self, task: dict) -> None:
        messages = list(self.analysis_phase.memory)
        search_results = self._extract_successful_search_results(messages)
        analysis_result = self._extract_analysis_result(messages)
        task_dict = dict(task)
        prelude_output = self.prelude_phase.output or {}
        if prelude_output.get('summary'):
            task_dict['event_summary'] = prelude_output['summary']
        if search_results:
            task_dict['search_results'] = [
                {'tool_name': sr['tool_name'], 'content': parse_json(sr['content'])}
                for sr in search_results
            ]
        if analysis_result:
            task_dict['analysis_summary'] = analysis_result
        self.handling_phase.input = render_task_xml(task_dict)

    def _run_phase(self, phase: Phase) -> None:
        phase.init_memory()
        if isinstance(phase, AskPhase):
            self._run_ask(phase)
        else:
            self._run_react(phase)

    def _run_ask(self, phase: AskPhase) -> None:
        step_start = time.monotonic()
        message, usage = self._request_llm_message_for_phase(phase, step=0)
        raw_content = (message.content or '').strip()
        parsed = parse_json(raw_content)
        phase.output = parsed if isinstance(parsed, dict) else {}

        observation = json.dumps(phase.output, ensure_ascii=False)
        self._record_action(
            phase=phase.name,
            result=phase.output.get('summary', json.dumps(phase.output, ensure_ascii=False)),
            action_type='event',
            action_status='completed',
            step=0,
            tool_name=f'generate_{phase.name}',
            observation=observation,
            statistics=self._build_statistics(step_start, usage),
        )

    def _run_react(self, phase: ReactPhase) -> None:
        """Run a single reasoning phase until the terminator tool is called or max steps reached."""
        for step in range(phase.max_steps):
            step_start = time.monotonic()
            message, usage = self._request_llm_message_for_phase(phase, step)

            tool_call = self._validate_single_tool_call(message.tool_calls, step)
            assistant_message = self._build_assistant_message(message, tool_call=tool_call)
            phase.memory += assistant_message

            if not tool_call:
                logger.warning(f'{phase.name} step {step} returned no tool call.')
                continue

            tool_name = tool_call.function.name
            logger.debug(f'Processing tool call {tool_name} at step {step}')
            self._process_tool_call(tool_call, phase, step_start=step_start, usage=usage, step=step)

            if tool_name == phase.terminator_tool:
                logger.debug(f'{phase.name} loop completed at step {step} via {tool_name}.')
                return

        logger.warning(
            f'{phase.name} loop reached max steps ({phase.max_steps}) '
            f'without calling {phase.terminator_tool!r}.'
        )
        self._force_terminator(phase)

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

    def _request_llm_message_for_phase(
        self,
        phase: Phase,
        step: int,
    ) -> tuple[Message, dict]:
        """Request an LLM message with phase-phaseific tools."""
        messages = list(phase.memory)
        total_chars = 0
        for i, m in enumerate(messages):
            content_len = len(str(m.get('content') or ''))
            logger.debug('[AgentRunner] step=%d msg[%d] role=%s content_chars=%d', step, i, m.get('role'), content_len)
            total_chars += content_len
        logger.debug('[AgentRunner] step=%d len(messages)=%d total_chars=%d (~%d tokens)', step, len(messages), total_chars, total_chars // 4)

        request_kwargs = {
            'messages': phase.memory,
            'context': self.ai_stats_context,
        }
        if isinstance(phase, ReactPhase) and phase.tools:
            request_kwargs.update({
                'tools': phase.tools,
                'tool_choice': 'auto',
            })
        elif isinstance(phase, AskPhase) and phase.schema:
            request_kwargs['response_format'] = gen_completion_format(phase.schema)

        response, usage, retry_entries = self._completion_with_retry(
            completion_kwargs=request_kwargs,
            step=step,
            phase=phase.name,
        )
        if retry_entries:
            usage = self._merge_retry_entries_into_usage(usage, retry_entries)
        return response.choices[0].message, usage

    def _force_terminator(self, phase: ReactPhase) -> dict:
        terminator_tool = next(
            (t for t in phase.tools if t.get('function', {}).get('name') == phase.terminator_tool),
            None,
        )
        if not terminator_tool:
            logger.warning(f'{phase.terminator_tool} tool not found in phase tools; skipping forced tool call.')
            return {}

        phase.memory += {'role': 'system', 'content': phase.terminator_prompt}

        step = phase.max_steps
        completion_kwargs = {
            'messages': phase.memory,
            'context': self.ai_stats_context,
            'tools': [terminator_tool],
            'tool_choice': {
                'type': 'function',
                'function': {
                    'name': phase.terminator_tool,
                },
            },
        }
        step_start = time.monotonic()
        response, usage, retry_entries = self._completion_with_retry(
            completion_kwargs=completion_kwargs,
            step=step,
            phase=phase.name,
            tool_name=phase.terminator_tool,
        )
        message = response.choices[0].message
        if retry_entries:
            usage = self._merge_retry_entries_into_usage(usage, retry_entries)

        tool_call = self._validate_single_tool_call(message.tool_calls, step)
        assistant_message = self._build_assistant_message(message, tool_call=tool_call)
        phase.memory += assistant_message

        if not tool_call:
            logger.warning(f'Forced {phase.terminator_tool} step {step} returned no tool call.')
            error_message = 'Forced tool call did not produce a tool call.'
            self._record_action(
                phase=phase,
                result=error_message,
                action_type='error',
                action_status='failed',
                record_phase_context=True,
                step=step,
                tool_name=phase.terminator_tool,
                statistics=self._build_statistics(step_start, usage),
            )
            raise AgentRetryExceededError(error_message)

        arguments = self._parse_tool_arguments(tool_call.function.arguments)
        self._process_tool_call(tool_call, phase, step_start=step_start, usage=usage, step=step)
        return arguments

    def _completion_with_retry(
        self,
        completion_kwargs: dict,
        step: int,
        phase: Phase,
        tool_name: str | None = None,
    ) -> tuple[object, dict, list[dict]]:
        retry_entries = []
        last_error = None
        last_usage = {}

        for current_retry in range(config.COMPLETION_MAX_RETRIES + 1):
            llm_start = time.monotonic()
            response = None
            try:
                response = self.llm_client.completion(**completion_kwargs)
                last_usage = self._extract_usage(response)
                if isinstance(phase, AskPhase) and phase.schema:
                    res = parse_json(response)
                    if not isinstance(res, dict) or not all([
                        required_field in res
                        for required_field in phase.schema.get('required')
                    ]):
                        raise ValueError("Invalid AI response: invalid format or missing required fields")
                return response, last_usage, retry_entries
            except Exception as e:
                last_error = e
                duration_ms = int((time.monotonic() - llm_start) * 1000)
                logger.exception(
                    'LLM call failed at %s step %s retry %s/%s: %s',
                    phase,
                    step,
                    current_retry,
                    config.COMPLETION_MAX_RETRIES,
                    e,
                )
                retry_entries.append({
                    'retry': current_retry,
                    'error': repr(e),
                    'duration_ms': duration_ms,
                    'token_usage': self._extract_usage(response),
                })
                if current_retry < config.COMPLETION_MAX_RETRIES:
                    time.sleep(config.COMPLETION_RETRY_INTERVAL)
                    continue

        usage_with_retries = self._merge_retry_entries_into_usage(last_usage, retry_entries)
        self._record_action(
            phase=phase,
            result=str(last_error) if last_error else 'LLM completion failed after retries.',
            action_type='error',
            action_status='failed',
            record_phase_context=True,
            step=step,
            tool_name=tool_name,
            statistics=self._build_statistics(None, usage_with_retries),
        )
        raise AgentRetryExceededError(str(last_error) if last_error else 'LLM completion failed after retries.')

    @staticmethod
    def _parse_tool_arguments(raw_arguments) -> dict:
        try:
            parsed_arguments = json.loads(raw_arguments) if isinstance(raw_arguments, str) else (raw_arguments or {})
        except (json.JSONDecodeError, TypeError):
            return {}
        return parsed_arguments if isinstance(parsed_arguments, dict) else {}

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
            combined_retries = list(stats.get('retries', []))
            combined_retries.extend(tool_token_usage.get('retries', []))
            if combined_retries:
                stats['retries'] = combined_retries
        if step_start is not None:
            stats['duration_ms'] = int((time.monotonic() - step_start) * 1000)
        return json.dumps(stats) if stats else None

    @staticmethod
    def _merge_retry_entries_into_usage(usage: dict | None, retry_entries: list[dict]) -> dict:
        merged_usage = dict(usage or {})
        if retry_entries:
            merged_usage['retries'] = retry_entries
        return merged_usage

    def _process_tool_call(
        self,
        tool_call: ChatCompletionMessageToolCall,
        phase: ReactPhase,
        step_start: float | None = None,
        usage: dict | None = None,
        step: int | None = None
    ) -> str | None:
        tool_name = tool_call.function.name
        is_final_answer = tool_name == 'final_answer'
        usage = usage or {}

        try:
            raw_arguments = tool_call.function.arguments
            parsed_arguments = json.loads(raw_arguments) if isinstance(raw_arguments, str) else (raw_arguments or {})
            if not isinstance(parsed_arguments, dict):
                raise TypeError('Tool arguments must be a JSON object.')
            arguments = parsed_arguments
        except (json.JSONDecodeError, TypeError) as e:
            arguments = {}
            logger.exception(f'Failed to parse tool arguments for {tool_name!r}: {e}')
            observation = json.dumps({
                'status': 'error',
                'tool_name': tool_name,
                'error_type': type(e).__name__,
                'error_message': str(e),
            }, ensure_ascii=False)
            self._record_action(
                phase=phase,
                result=str(e),
                action_type='error',
                action_status='failed',
                step=step,
                tool_name=tool_name,
                tool_arguments=arguments,
                statistics=self._build_statistics(step_start, usage),
            )
            logger.debug(f'Observation: {observation}')
            if is_final_answer:
                return observation
            phase.memory += {
                'role': 'tool',
                'tool_call_id': tool_call.id,
                'content': observation,
            }
            return None

        logger.debug(f"Calling tool: '{tool_name}' with arguments: {arguments}")
        self.tool_executor.thought_process.add_tool_calls_group()
        retry_entries = []
        tool_failed = False
        try:
            result = None
            for current_retry in range(config.TOOL_CALL_MAX_RETRIES + 1):
                tool_try_start = time.monotonic()
                try:
                    result = self.tool_executor.execute(tool_call)
                    break
                except Exception as e:
                    logger.exception(
                        'Tool execution failed for %r at %s step %s retry %s/%s: %s',
                        tool_name,
                        phase.name,
                        step,
                        current_retry,
                        config.TOOL_CALL_MAX_RETRIES,
                        e,
                    )
                    retry_entries.append({
                        'retry': current_retry,
                        'reason': 'tool_call_error',
                        'error': repr(e),
                        'duration_ms': int((time.monotonic() - tool_try_start) * 1000),
                    })
                    if current_retry < config.TOOL_CALL_MAX_RETRIES:
                        time.sleep(config.TOOL_CALL_RETRY_INTERVAL)
                        continue
                    raise

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
            observation = json.dumps({
                'status': 'error',
                'tool_name': tool_name,
                'error_type': type(e).__name__,
                'error_message': str(e),
            }, ensure_ascii=False)
        finally:
            tool_token_usage = {}
            if self.tool_executor.thought_process.actions:
                last_group = self.tool_executor.thought_process.actions.pop()
                tool_token_usage = last_group.get('token_usage', {})
        if retry_entries:
            tool_token_usage = self._merge_retry_entries_into_usage(tool_token_usage, retry_entries)
        logger.debug(f'Observation: {observation}')

        statistics = self._build_statistics(step_start, usage, tool_token_usage=tool_token_usage)
        if tool_failed:
            self._record_action(
                phase=phase,
                result=json.loads(observation).get('error_message', observation),
                action_type='error',
                action_status='failed',
                step=step,
                tool_name=tool_name,
                tool_arguments=arguments,
                observation=observation,
                statistics=statistics,
            )
        else:
            self._record_tool_action(phase, step, tool_name, arguments, observation, statistics=statistics,)

        if is_final_answer:
            return observation

        phase.memory += {
            'role': 'tool',
            'tool_call_id': tool_call.id,
            'content': observation,
        }

    def _record_tool_action(
        self,
        phase: Phase,
        step: int,
        tool_name: str,
        tool_arguments: dict,
        observation: str,
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
        observation=None,
        suggestion_content=None,
        sources=None,
        statistics=None,
        record_phase_context=False,
    ):
        """Record an action to SeaDB via the action_recorder callback."""
        if not self.action_recorder:
            return

        prompt = phase.system_prompt if phase else ''
        phase_input = phase.input if phase else ''

        action_data = {
            'run_id': self.run_id,
            'source_type': self.record_context.get('source_type'),
            'source_id': self.record_context.get('source_id'),
            'source_title': self.record_context.get('source_title'),
            'phase': phase,
            'record_phase_context': record_phase_context,
            'prompt': prompt,
            'input': phase_input,
            'result': result,
            'action_type': action_type,
            'action_status': action_status,
            'step': step,
            'tool_name': tool_name,
            'tool_arguments': tool_arguments,
            'observation': observation or '',
            'suggestion_content': suggestion_content or '',
            'sources': sources or [],
            'statistics': statistics,
        }
            
        try:
            self.action_recorder(**action_data)
        except Exception as e:
            logger.warning(f'Failed to record action: {e}')
