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
import re
from seaqa_ai import config
from seaqa_ai.utils.llm_api import CompletionAPI, ResponsesAPI, ResponsesResult
from seaqa_ai.utils.tools import InvalidToolArgumentsException, OpenAIToolExecutor
from seaqa_ai.agent.utils import render_task_xml
from seaqa_ai.agent.utils.phase import Phase, AskPhase, ReactPhase
from seaqa_ai.utils import parse_json, remove_sources_content_and_snippets, get_answer_and_sources, gen_completion_format

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
        completion_client: CompletionAPI,
        responses_client: ResponsesAPI,
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
            completion_client: CompletionAPI client used for AskPhase calls.
            responses_client: ResponsesAPI client used for ReactPhase calls.
            record_context: dict with keys source_type, source_id, source_title.
            ai_stats_context: dict with keys used for AI usage statistics logging
                            (e.g. org_id, project_uuid).
            action_recorder: callable(run_id, source_type, source_id, source_title,
                             action_type, tool_name, content, result, action_status,
                             suggestion_title, references, statistics) -> int
            run_id: current agent run _pk in SeaDB.
            tool_metadata: dict mapping tool_name to {action_type, action_status, suggestion_title}.
            prelude_phase: AskPhase for the prelude event-summary step.
            analysis_phase: ReactPhase for the analysis phase.
            handling_phase: ReactPhase for the handling phase.
        """
        self.tool_executor = tool_executor
        self.completion_client = completion_client
        self.responses_client = responses_client
        self.record_context = record_context
        self.ai_stats_context = ai_stats_context or {}
        self.action_recorder = action_recorder
        self.run_id = run_id
        self.tool_metadata = tool_metadata or {}
        self.prelude_phase = prelude_phase
        self.analysis_phase = analysis_phase
        self.handling_phase = handling_phase
        # Collect pending suggestion actions produced in this run so the caller
        # can decide which ones to auto-execute. Each entry: {'action_id', 'tool_name'}.
        self.pending_suggestion_actions: list[dict] = []

    def run(self, task: dict) -> None:
        """Drive the prelude, analysis, and handling flow.

        Args:
            task: structured task payload (source_id, event, record, ...).
        """

        self._run_prelude_phase(task)
        
        self._run_analysis_phase(task)

        if self._should_skip_handling_phase():
            return

        self._run_handling_phase(task)

    def _run_prelude_phase(self, task: dict) -> None:
        self.prelude_phase.input = render_task_xml(task)
        self._run_phase(self.prelude_phase)

    def _run_analysis_phase(self, task: dict) -> None:
        self._update_analysis_input(task)
        self._run_phase(self.analysis_phase)

    def _should_skip_handling_phase(self) -> bool:
        analysis_result = self._extract_analysis_result(list(self.analysis_phase.memory))
        recommended_actions = self._extract_recommended_actions(analysis_result)
        if not recommended_actions or set(recommended_actions) == {'no_op'}:
            logger.info('Analysis recommended no handling action; skipping handling phase.')
            return True
        return False

    @staticmethod
    def _extract_recommended_actions(analysis_result: dict) -> list[str]:
        if not isinstance(analysis_result, dict):
            return []
        actions = []
        primary = str(analysis_result.get('recommended_action') or '').strip()
        if primary:
            actions.append(primary)
        extra = analysis_result.get('recommended_actions')
        if isinstance(extra, list):
            for value in extra:
                item = str(value or '').strip()
                if item:
                    actions.append(item)
        deduped = []
        seen = set()
        for action in actions:
            if action in seen:
                continue
            seen.add(action)
            deduped.append(action)
        return deduped

    def _run_handling_phase(self, task: dict) -> None:
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
        analysis_result = self._extract_analysis_result(messages)
        task_dict = dict(task)
        prelude_output = self.prelude_phase.output or {}
        if prelude_output.get('summary'):
            task_dict['event_summary'] = prelude_output['summary']
        search_results = self._get_relevant_search_results(analysis_result)
        if search_results:
            task_dict['search_results'] = search_results
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
        content, usage = self._request_ask_phase_llm_result(phase, step=0)
        raw_content = (content or '').strip()
        parsed = parse_json(raw_content)
        phase.output = parsed if isinstance(parsed, dict) else {}

        observation = json.dumps(phase.output, ensure_ascii=False)
        self._record_action(
            phase=phase,
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
            result, usage = self._request_react_phase_llm_result(phase, step)

            tool_calls = self._validate_tool_calls(result.tool_calls, phase, step)
            phase.memory += self._build_assistant_items(result)

            if not tool_calls:
                logger.warning(f'{phase.name} step {step} returned no tool call.')
                continue

            for tool_index, tool_call in enumerate(tool_calls):
                # Add calls incrementally so every call in memory receives a matching output.
                phase.memory += tool_call
                tool_name = tool_call['name']
                logger.debug(f'Processing tool call {tool_name} at step {step}')
                tool_usage = usage if tool_index == 0 else {}
                tool_step_start = step_start if tool_index == 0 else time.monotonic()
                tool_succeeded = self._process_tool_call(tool_call, phase, step_start=tool_step_start, usage=tool_usage, step=step)

                if not tool_succeeded:
                    logger.warning(f'{phase.name} tool call {tool_name} at step {step} failed.')
                    # Stop this batch so the next step can correct it using the error observation.
                    # Especially important for invalid tool arguments which need to be corrected.
                    break

                if tool_name == phase.terminator_tool:
                    logger.debug(f'{phase.name} loop completed at step {step} via {tool_name}.')
                    return

        logger.warning(
            f'{phase.name} loop reached max steps ({phase.max_steps}) '
            f'without calling {phase.terminator_tool!r}.'
        )
        self._force_terminator(phase)
  
    def _get_relevant_search_results(self, analysis_result: dict) -> list[dict]:
        if not isinstance(analysis_result, dict):
            return []

        labels = analysis_result.get('relevant_reference_labels') or []
        if not isinstance(labels, list):
            return []

        # expand grouped labels to compatible with <reference_1, reference_2, ...>
        labels = [
            expanded_label
            for label in labels
            for expanded_label in self._expand_grouped_reference_label(label)
        ]

        sources_results = self.tool_executor.cache.get('sources_results', [])
        selected_results = []
        seen_indexes = set()

        for label in labels:
            source_index = self._parse_reference_label(label)
            if source_index is None or source_index in seen_indexes:
                continue
            if source_index < 0 or source_index >= len(sources_results):
                logger.warning('Ignoring out-of-range relevant reference label: %s', label)
                continue

            seen_indexes.add(source_index)
            source = sources_results[source_index]
            if not isinstance(source, dict):
                continue
            selected_results.append(source)

        return selected_results

    @staticmethod
    def _expand_grouped_reference_label(label) -> list[str]:
        """<reference_1, reference_2, ...> -> [<reference_1>, <reference_2>, ...]"""
        pattern = re.compile(r'\<reference_\d+(?:\s*,\s*reference_\d+)+\>')
    
        def replacer(match):
            indexes = re.findall(r'reference_(\d+)', match.group(0))
            return ', '.join(f'<reference_{idx}>' for idx in indexes)

        expanded = re.sub(pattern, replacer, label)
        return re.findall(r'<reference_\d+>', expanded)

    @staticmethod
    def _parse_reference_label(label: str) -> int | None:
        if not isinstance(label, str):
            return None
        if not label.startswith('<reference_') or not label.endswith('>'):
            return None
        try:
            return int(label[len('<reference_'):-1])
        except ValueError:
            return None

    def _extract_analysis_result(self, messages: list[dict]) -> dict:
        for msg in reversed(messages):
            if msg.get('type') != 'function_call':
                continue

            tool_name = msg.get('name', '')
            if tool_name == self.analysis_phase.terminator_tool:
                return self._parse_tool_arguments(msg.get('arguments'))
        return {}

    def _request_ask_phase_llm_result(
        self,
        phase: AskPhase,
        step: int,
    ) -> tuple[str, dict]:
        """Request the single-turn AskPhase result through Chat Completions."""
        messages = list(phase.memory)
        total_chars = 0
        for i, m in enumerate(messages):
            content_len = len(str(m.get('content') or ''))
            logger.debug('[AgentRunner] step=%d msg[%d] role=%s content_chars=%d', step, i, m.get('role'), content_len)
            total_chars += content_len
        logger.debug('[AgentRunner] step=%d len(messages)=%d total_chars=%d (~%d tokens)', step, len(messages), total_chars, total_chars // 4)

        completion_kwargs = {
            'messages': messages,
            'context': self.ai_stats_context,
        }
        if phase.schema:
            completion_kwargs['response_format'] = gen_completion_format(phase.schema)

        content, usage, retry_entries = self._ask_completion_with_retry(
            completion_kwargs=completion_kwargs,
            step=step,
            phase=phase,
        )
        if retry_entries:
            usage = self._merge_retry_entries_into_usage(usage, retry_entries)
        return content, usage

    def _request_react_phase_llm_result(
        self,
        phase: ReactPhase,
        step: int,
    ) -> tuple[ResponsesResult, dict]:
        """Request one ReAct LLM step through Responses."""
        messages = list(phase.memory)
        total_chars = 0
        for i, m in enumerate(messages):
            content_len = len(str(m.get('content') or ''))
            logger.debug('[AgentRunner] step=%d msg[%d] role=%s content_chars=%d', step, i, m.get('role'), content_len)
            total_chars += content_len
        logger.debug('[AgentRunner] step=%d len(messages)=%d total_chars=%d (~%d tokens)', step, len(messages), total_chars, total_chars // 4)

        responses_kwargs = {
            'input': messages,
            'context': self.ai_stats_context,
        }
        if phase.tools:
            responses_kwargs.update({
                'tools': phase.tools,
                'tool_choice': 'auto',
            })

        result, usage, retry_entries = self._react_responses_with_retry(
            responses_kwargs=responses_kwargs,
            step=step,
            phase=phase,
        )
        if retry_entries:
            usage = self._merge_retry_entries_into_usage(usage, retry_entries)
        return result, usage

    def _force_terminator(self, phase: ReactPhase) -> dict:
        terminator_tool = next(
            (tool for tool in phase.tools if tool.get('name') == phase.terminator_tool),
            None,
        )
        if not terminator_tool:
            logger.warning(f'{phase.terminator_tool} tool not found in phase tools; skipping forced tool call.')
            return {}

        phase.memory += {'role': 'system', 'content': phase.terminator_prompt}

        step = phase.max_steps
        responses_kwargs = {
            'input': list(phase.memory),
            'context': self.ai_stats_context,
            'tools': [terminator_tool],
            'tool_choice': {
                'type': 'function',
                'name': phase.terminator_tool,
            },
        }
        step_start = time.monotonic()
        result, usage, retry_entries = self._react_responses_with_retry(
            responses_kwargs=responses_kwargs,
            step=step,
            phase=phase,
            tool_name=phase.terminator_tool,
        )
        if retry_entries:
            usage = self._merge_retry_entries_into_usage(usage, retry_entries)

        tool_call = self._validate_tool_calls(result.tool_calls, phase, step)[0] if result.tool_calls else None
        phase.memory += self._build_assistant_items(result, tool_calls=[tool_call] if tool_call else [])

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

        arguments = self._parse_tool_arguments(tool_call.get('arguments'))
        tool_succeeded = self._process_tool_call(tool_call, phase, step_start=step_start, usage=usage, step=step)
        if not tool_succeeded:
            raise AgentRetryExceededError(f'Forced {phase.terminator_tool} tool call failed after the agent exhausted its steps.')
        return arguments

    def _ask_completion_with_retry(
        self,
        completion_kwargs: dict,
        step: int,
        phase: AskPhase,
    ) -> tuple[str, dict, list[dict]]:
        retry_entries = []
        last_error = None
        last_usage = {}

        for current_retry in range(config.COMPLETION_MAX_RETRIES + 1):
            llm_start = time.monotonic()
            response = None
            try:
                response = self.completion_client.completion(**completion_kwargs)
                content = response.choices[0].message.content or ''
                last_usage = self._extract_usage(response)
                if phase.schema:
                    schema_output = parse_json(content)
                    required_fields = phase.schema.get('required') or []
                    if not isinstance(schema_output, dict) or not all(
                        required_field in schema_output
                        for required_field in required_fields
                    ):
                        raise ValueError('Invalid AI response: invalid format or missing required fields')
                return content, last_usage, retry_entries
            except Exception as e:
                last_error = e
                duration_ms = int((time.monotonic() - llm_start) * 1000)
                logger.exception(
                    'LLM completion call failed at %s step %s retry %s/%s: %s',
                    phase.name,
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
            result=str(last_error) if last_error else 'LLM completion call failed after retries.',
            action_type='error',
            action_status='failed',
            record_phase_context=True,
            step=step,
            statistics=self._build_statistics(None, usage_with_retries),
        )
        raise AgentRetryExceededError(str(last_error) if last_error else 'LLM completion call failed after retries.')

    def _react_responses_with_retry(
        self,
        responses_kwargs: dict,
        step: int,
        phase: ReactPhase,
        tool_name: str | None = None,
    ) -> tuple[ResponsesResult, dict, list[dict]]:
        retry_entries = []
        last_error = None
        last_usage = {}

        for current_retry in range(config.COMPLETION_MAX_RETRIES + 1):
            llm_start = time.monotonic()
            result = None
            try:
                result = self.responses_client.responses(**responses_kwargs)
                last_usage = result.usage
                return result, last_usage, retry_entries
            except Exception as e:
                last_error = e
                duration_ms = int((time.monotonic() - llm_start) * 1000)
                logger.exception(
                    'LLM responses call failed at %s step %s retry %s/%s: %s',
                    phase.name,
                    step,
                    current_retry,
                    config.COMPLETION_MAX_RETRIES,
                    e,
                )
                retry_entries.append({
                    'retry': current_retry,
                    'error': repr(e),
                    'duration_ms': duration_ms,
                    'token_usage': result.usage if result else {},
                })
                if current_retry < config.COMPLETION_MAX_RETRIES:
                    time.sleep(config.COMPLETION_RETRY_INTERVAL)
                    continue

        usage_with_retries = self._merge_retry_entries_into_usage(last_usage, retry_entries)
        self._record_action(
            phase=phase,
            result=str(last_error) if last_error else 'LLM responses call failed after retries.',
            action_type='error',
            action_status='failed',
            record_phase_context=True,
            step=step,
            tool_name=tool_name,
            statistics=self._build_statistics(None, usage_with_retries),
        )
        raise AgentRetryExceededError(str(last_error) if last_error else 'LLM responses call failed after retries.')

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
    def _parse_tool_arguments(raw_arguments) -> dict:
        try:
            parsed_arguments = json.loads(raw_arguments) if isinstance(raw_arguments, str) else (raw_arguments or {})
        except (json.JSONDecodeError, TypeError):
            return {}
        return parsed_arguments if isinstance(parsed_arguments, dict) else {}

    def _validate_tool_calls(
        self,
        tool_calls: list[dict] | None,
        phase: ReactPhase,
        step: int,
    ) -> list[dict]:
        """Return executable tool calls and preserve terminator phase boundaries."""
        if not tool_calls:
            return []

        tool_names = [tool_call.get('name', '') for tool_call in tool_calls]
        terminator_indexes = [
            index
            for index, tool_call in enumerate(tool_calls)
            if tool_call.get('name') == phase.terminator_tool
        ]
        if not terminator_indexes:
            return tool_calls

        first_terminator_index = terminator_indexes[0]
        if first_terminator_index == 0:
            if len(tool_calls) > 1:
                ignored_names = tool_names[1:]
                logger.warning(
                    'Agent %s step %s returned tool calls after terminator %r; ignoring trailing tools. ignored_tool_names=%s',
                    phase.name,
                    step,
                    phase.terminator_tool,
                    ignored_names,
                )
                return tool_calls[:1]
            return tool_calls

        ignored_names = tool_names[first_terminator_index:]
        logger.warning(
            'Agent %s step %s returned terminator %r with other tool calls. '
            'Ignoring the premature terminator so it can be called after tool outputs. ignored_tool_names=%s',
            phase.name,
            step,
            phase.terminator_tool,
            ignored_names,
        )
        return tool_calls[:first_terminator_index]

    @staticmethod
    def _build_assistant_items(
        message: ResponsesResult,
        tool_calls: list[dict] | None = None,
    ) -> list[dict]:
        items = []
        if message.content:
            items.append({'role': 'assistant', 'content': message.content})
        for tool_call in tool_calls or []:
            items.append(tool_call)
        return items

    @staticmethod
    def _build_statistics(
        step_start: float | None,
        usage: dict | None,
        tool_token_usage: dict | None = None,
    ) -> str | None:
        """Build a JSON string with token usage and duration for one step.

        usage: tokens from the LLM Responses call.
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
        tool_call: dict,
        phase: ReactPhase,
        step_start: float | None = None,
        usage: dict | None = None,
        step: int | None = None,
    ) -> bool:
        tool_name = tool_call['name']
        usage = usage or {}

        try:
            raw_arguments = tool_call.get('arguments')
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
            phase.memory += {
                'type': 'function_call_output',
                'call_id': tool_call.get('call_id', ''),
                'output': observation,
            }
            return False

        logger.debug(f"Calling tool: '{tool_name}' with arguments: {arguments}")
        self.tool_executor.thought_process.add_tool_calls_group()
        retry_entries = []
        tool_failed = False
        raw_result = None
        try:
            result = None
            for current_retry in range(config.TOOL_CALL_MAX_RETRIES + 1):
                tool_try_start = time.monotonic()
                try:
                    result = self.tool_executor.execute(tool_call)
                    break
                except InvalidToolArgumentsException as e:
                    logger.warning('Invalid model arguments for %r at %s step %s: %s', tool_name, phase.name, step, e)
                    raise
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

            raw_result = result
            if raw_result is None:
                observation = ''
            elif isinstance(raw_result, str):
                observation = raw_result.strip()
            elif isinstance(raw_result, dict):
                message = raw_result.get('message') or raw_result.get('result') or ''
                if isinstance(message, str) and message.strip():
                    observation = message.strip()
                else:
                    observation = json.dumps(raw_result, ensure_ascii=False)
            elif isinstance(raw_result, list):
                observation = json.dumps(raw_result, ensure_ascii=False)
            else:
                observation = str(raw_result).strip()
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
            self._record_tool_action(phase, step, tool_name, arguments, observation, raw_result=raw_result, statistics=statistics)

        phase.memory += {
            'type': 'function_call_output',
            'call_id': tool_call.get('call_id', ''),
            'output': observation,
        }
        return not tool_failed

    def _record_tool_action(
        self,
        phase: Phase,
        step: int,
        tool_name: str,
        tool_arguments: dict,
        observation: str,
        raw_result=None,
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
        recorded_tool_name = tool_name
        if action_type == 'suggestion':
            getter = metadata.get('get_suggestion_title')
            if callable(getter):
                suggestion_title = getter(**tool_arguments)
            else:
                suggestion_title = metadata.get('suggestion_title', '')
        else:
            suggestion_title = ''

        sources = []
        if action_type == 'analysis':
            try:
                result, sources = get_answer_and_sources(self.tool_executor, tool_arguments.get('analysis'))
                sources = remove_sources_content_and_snippets(sources)
            except Exception as e:
                logger.warning('Failed to parse analysis references, keep raw content. tool=%s error=%s', tool_name, e)
                result = tool_arguments.get('analysis') or ''
                sources = []
        else:
            # For documents_search & issues_search, use observation instead of result
            # For suggestion tools, use result to store execution result.
            result = ''

        content = (
            tool_arguments.get('analysis') or
            tool_arguments.get('reply') or
            tool_arguments.get('message') or
            ''
        )
        
        suggestion_content = content if action_type == 'suggestion' else ''
        if tool_name == 'suggest_create_ticket':
            suggestion_content = ''
            if isinstance(raw_result, dict):
                suggestion_content = raw_result.get('suggestion_content')

        if self._try_record_multi_target_suggestion_actions(
            phase=phase,
            step=step,
            tool_name=tool_name,
            tool_arguments=tool_arguments,
            metadata=metadata,
            action_type=action_type,
            action_status=action_status,
            observation=observation,
            content=content,
            result=result,
            references=sources,
            statistics=statistics,
            default_tool_name=recorded_tool_name,
            default_suggestion_title=suggestion_title,
        ):
            return

        action_id = self._record_action(
            phase=phase,
            result=result,
            action_type=action_type,
            action_status=action_status,
            step=step,
            tool_name=recorded_tool_name,
            tool_arguments=tool_arguments,
            observation=observation,
            suggestion_reason=tool_arguments.get('reason') if action_type == 'suggestion' else '',
            suggestion_title=suggestion_title if action_type == 'suggestion' else '',
            suggestion_content=suggestion_content,
            references=sources,
            statistics=statistics,
        )

        if action_id and action_type == 'suggestion' and action_status == 'pending':
            self.pending_suggestion_actions.append({
                'action_id': action_id,
                'tool_name': recorded_tool_name,
            })

    def _try_record_multi_target_suggestion_actions(
        self,
        *,
        phase: Phase,
        step: int,
        tool_name: str,
        tool_arguments: dict,
        metadata: dict,
        action_type: str,
        action_status: str,
        observation: str,
        content: str,
        result: str,
        references: list,
        statistics: str | None,
        default_tool_name: str,
        default_suggestion_title: str,
    ) -> bool:
        multi_target_resolver = metadata.get('resolve_action_targets')
        if action_type != 'suggestion' or not callable(multi_target_resolver):
            return False

        try:
            targets = multi_target_resolver(**tool_arguments)
        except Exception as e:
            logger.warning('Failed to resolve action targets. tool=%s error=%s', tool_name, e)
            return False

        if not targets:
            return False

        self._record_multi_target_suggestion_actions(
            phase=phase,
            step=step,
            action_type=action_type,
            action_status=action_status,
            tool_arguments=tool_arguments,
            observation=observation,
            content=content,
            result=result,
            references=sources,
            statistics=statistics,
            targets=targets,
            default_tool_name=default_tool_name,
            default_suggestion_title=default_suggestion_title,
        )
        return True
    
    def _record_multi_target_suggestion_actions(
        self,
        *,
        phase: Phase,
        step: int,
        action_type: str,
        action_status: str,
        tool_arguments: dict,
        observation: str,
        content: str,
        result: str,
        references: list,
        statistics: str | None,
        targets,
        default_tool_name: str,
        default_suggestion_title: str,
    ) -> None:
        for target in targets:
            if not isinstance(target, dict):
                continue
            target_item_type = target.get('source_type') or None
            target_item_id = target.get('source_id') or None
            target_item_title = target.get('source_title') or None
            target_tool_name = target.get('tool_name') or default_tool_name
            target_suggestion_title = target.get('suggestion_title') or default_suggestion_title
            action_id = self._record_action(
                phase=phase,
                result=result,
                action_type=action_type,
                action_status=action_status,
                step=step,
                tool_name=target_tool_name,
                tool_arguments=tool_arguments,
                observation=observation,
                suggestion_title=target_suggestion_title,
                suggestion_content=content,
                references=sources,
                statistics=statistics,
                source_type_override=target_item_type,
                source_id_override=target_item_id,
                source_title_override=target_item_title,
            )
            if action_id and action_status == 'pending':
                self.pending_suggestion_actions.append({
                    'action_id': action_id,
                    'tool_name': target_tool_name,
                })

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
        suggestion_reason=None,
        suggestion_title=None,
        suggestion_content=None,
        references=None,
        statistics=None,
        record_phase_context=False,
        source_type_override=None,
        source_id_override=None,
        source_title_override=None,
    ):
        """Record an action to SeaDB via the action_recorder callback.

        Returns the recorded action_id (pk) when available, else None.
        """
        if not self.action_recorder:
            return None

        prompt = phase.system_prompt if phase else ''
        phase_input = phase.input if phase else ''

        action_data = {
            'run_id': self.run_id,
            'source_type': source_type_override or self.record_context.get('source_type'),
            'source_id': source_id_override or self.record_context.get('source_id'),
            'source_title': source_title_override or self.record_context.get('source_title'),
            'phase': phase.name,
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
            'suggestion_reason': suggestion_reason or '',
            'suggestion_title': suggestion_title or '',
            'suggestion_content': suggestion_content or '',
            'references': references or [],
            'statistics': statistics,
        }
            
        try:
            return self.action_recorder(**action_data)
        except Exception as e:
            logger.warning(f'Failed to record action: {e}')
            return None
