"""Atomic ReACT execution loop shared by workflow phases and other callers."""
import inspect
import json
import logging
import time
import uuid

from seaqa_ai import config
from seaqa_ai.agent.utils.final_output_schema_utils import FinalOutputSchemaUtils
from seaqa_ai.agent.utils.record_identity import validate_record_identity
from seaqa_ai.agent.tools.get_record import GetRecord
from seaqa_ai.utils import (
    get_answer_and_sources,
    remove_sources_content_and_snippets,
    sanitize_project_prompt,
)
from seaqa_ai.utils.llm_api import ResponsesResult
from seaqa_ai.utils.memory import OpenAIMemory
from seaqa_ai.utils.tools import (
    BasicTool,
    InvalidToolArgumentsException,
    OpenAIToolExecutor,
)


logger = logging.getLogger(__name__)


class AgentRetryExceededError(Exception):
    """Raised when an LLM request cannot succeed within its retry budget."""


class AgentFinalOutputSchemaError(ValueError):
    """Raised when the agent cannot produce output matching its JSON schema."""


class AgentReACT:
    """Run one isolated LLM tool-calling loop.

    A loop owns its memory, executor, and tool instances so separate workflow
    phases cannot leak tool state or conversation history.
    """

    MAX_RECORDS_INPUT = 20

    def __init__(
        self,
        app,
        responses_client,
        phase_spec,
        react_tracer,
        *,
        project_prompt='',
        completion_max_retries=None,
        completion_retry_interval=None,
        tool_call_max_retries=None,
        tool_call_retry_interval=None,
    ):
        self.app = app
        self.responses_client = responses_client
        self.phase_spec = phase_spec
        self.context = dict(phase_spec.context or {})
        self.system_prompts = [phase_spec.get_system_prompt()]
        if project_prompt and project_prompt.strip():
            self.system_prompts.append(
                '<project_context>\n'
                f'{sanitize_project_prompt(project_prompt)}\n'
                '</project_context>'
            )
        language_instruction = self._build_language_instruction(self.context.get('target_language'))
        if language_instruction:
            self.system_prompts.append(language_instruction)
        self.message_input = phase_spec.message_input
        self.react_tracer = react_tracer
        self.final_output_schema = phase_spec.final_output_schema or {}
        if self.final_output_schema:
            self.system_prompts.append(
                '<final_output_format>\n'
                f'{self._schema_prompt()}\n'
                '</final_output_format>'
            )
        self.max_steps = phase_spec.max_steps
        self.max_steps_prompt = phase_spec.max_steps_prompt

        self.records_input = self._validate_records_input(phase_spec.records_input or [])

        self.skill = phase_spec.skill
        self.completion_max_retries = (
            config.COMPLETION_MAX_RETRIES
            if completion_max_retries is None else completion_max_retries
        )
        self.completion_retry_interval = (
            config.COMPLETION_RETRY_INTERVAL
            if completion_retry_interval is None else completion_retry_interval
        )
        self.tool_call_max_retries = (
            config.TOOL_CALL_MAX_RETRIES
            if tool_call_max_retries is None else tool_call_max_retries
        )
        self.tool_call_retry_interval = (
            config.TOOL_CALL_RETRY_INTERVAL
            if tool_call_retry_interval is None else tool_call_retry_interval
        )

        self.tool_executor = OpenAIToolExecutor()
        self._tool_instances = {}
        self._register_tools(phase_spec.tools)

        memory_system_prompts = [
            {'role': 'system', 'content': system_prompt}
            for system_prompt in self.system_prompts
        ]
        self.memory = OpenAIMemory(memory_system_prompts)
        self.memory += {'role': 'user', 'content': phase_spec.message_input}


        self._inject_records_input()

        self._inject_skill_input()

    def run(self):
        """Execute until the model returns final text, then validate it if needed."""
        step = 0
        max_steps_prompt_added = False
        while True:
            if self.max_steps and step == self.max_steps - 1 and not max_steps_prompt_added:
                self.memory += {'role': 'system', 'content': self.max_steps_prompt}
                max_steps_prompt_added = True

            result, usage, retries = self._request_response(
                tools=[] if self.max_steps and step >= self.max_steps else self.tool_executor.get(),
                step=step,
            )
            self._append_assistant_result(result)

            if not result.tool_calls:
                return self._validate_final_output(result.content, step, usage, retries)

            if self.max_steps and step == self.max_steps - 1:
                self._simulate_max_steps_tool_calls(result.tool_calls, step, usage, retries)
                step += 1
                continue

            if self.max_steps and step >= self.max_steps:
                raise AgentRetryExceededError(
                    'The model returned tool calls after tool use was disabled: '
                    f'{self.max_steps_prompt}'
                )

            self._execute_tool_calls(result.tool_calls, step, usage, retries)
            step += 1

    ############# input operations
    @staticmethod
    def _validate_records_input(records_input):
        seen_records = set()
        def __validate_records(records):
            result = []
            for index, record in enumerate(records):
                if not isinstance(record, dict):
                    raise TypeError(f'records_input[{index}] must be an object')
                required = {'source_type', 'connection_id', 'record_id'}
                missing = required - set(record)
                if missing:
                    raise ValueError(
                        f'records_input[{index}] missing required fields: {", ".join(sorted(missing))}'
                    )
                try:
                    source_type = validate_record_identity(
                        record['source_type'], record['connection_id'], record['record_id'],
                    )
                except ValueError as error:
                    raise ValueError(f'records_input[{index}] has an invalid identity: {error}') from error
                record_key = (
                    source_type,
                    record['connection_id'],
                    record['record_id'],
                )
                if record_key not in seen_records:
                    seen_records.add(record_key)
                    result.append(record)
            return result

        if isinstance(records_input, dict):
            result = {}
            for prompt, records in records_input.items():
                if not isinstance(prompt, str):
                    raise TypeError('records_input prompts must be strings')
                if not isinstance(records, (list, tuple)):
                    raise TypeError('records_input groups must be lists or tuples')
                result[prompt] = __validate_records(records)
            return result
        elif isinstance(records_input, (list, tuple)):
            return __validate_records(records_input)
        raise TypeError('records_input must be a list, tuple, or dictionary')

    def _inject_records_input(self):
        if self.records_input and not self.context.get('project_uuid'):
            raise ValueError('context.project_uuid is required when records_input is provided')

        call_index = 0
        def __inject_records(records):
            nonlocal call_index
            for record in records:
                arguments = {
                    'source_type': record['source_type'],
                    'connection_id': record['connection_id'],
                    'record_id': record['record_id'],
                }
                started_at = time.monotonic()
                record_call = {
                    'type': 'function_call',
                    'call_id': f'records_input_{call_index}',
                    'name': 'get_record',
                    'arguments': json.dumps(arguments, ensure_ascii=False),
                }
                self.memory += record_call
                try:
                    result = GetRecord().execute(
                        **arguments,
                        project_uuid=self.context['project_uuid'],
                        app=self.app,
                        record_cache=self.context.get('record_cache'),
                        context=self.context,
                    )
                except Exception as error:
                    observation = self._tool_error_observation('get_record', error)
                    self._trace(
                        action_type='error', action_status='failed', step=0,
                        tool_name=record_call['name'], tool_arguments=arguments,
                        observation=observation, result=str(error),
                        statistics=self._statistics(started_at, {
                            'input_tokens': 0, 'output_tokens': 0, 'total_tokens': 0,
                        }, []),
                    )
                else:
                    observation = self._format_observation(result)
                    self._trace(
                        action_type='tool_call', action_status='completed', step=0,
                        tool_name=record_call['name'], tool_arguments=arguments,
                        observation=observation, result=result,
                        statistics=self._statistics(started_at, {
                            'input_tokens': 0, 'output_tokens': 0, 'total_tokens': 0,
                        }, []),
                    )
                self._append_tool_output(record_call, observation)
                call_index += 1

        if isinstance(self.records_input, dict):
            for prompt, records in self.records_input.items():
                self.memory += {
                    'role': 'system',
                    'content': prompt,
                }
                __inject_records(records)
        elif isinstance(self.records_input, (list, tuple)):
            return __inject_records(self.records_input)

    def _inject_skill_input(self):
        if self.skill is not None:
            started_at = time.monotonic()
            arguments = {
                'event': self.phase_spec.event,
                'phase': self.phase_spec.phase,
            }
            skill_call = {
                'type': 'function_call',
                'call_id': 'load_event_skill',
                'name': 'load_event_skill',
                'arguments': json.dumps(arguments, ensure_ascii=False),
            }
            observation = self._format_observation(self.skill)
            self._trace(
                action_type='tool_call', action_status='completed', step=0,
                tool_name=skill_call['name'], tool_arguments=arguments,
                observation=observation, result=self.skill,
                statistics=self._statistics(started_at, {
                    'input_tokens': 0, 'output_tokens': 0, 'total_tokens': 0,
                }, []),
            )
            self.memory += skill_call
            self._append_tool_output(skill_call, observation)


    ############# memory operations
    def _request_response(self, *, tools, step):
        retries = []
        last_error = None
        last_usage = {}
        for retry in range(self.completion_max_retries + 1):
            started_at = time.monotonic()
            try:
                response_kwargs = {}
                if self.final_output_schema:
                    response_schema = FinalOutputSchemaUtils.to_strict_response_schema(
                        self.final_output_schema,
                    )
                    response_kwargs['text'] = {
                        'format': {
                            'type': 'json_schema',
                            'name': 'agent_final_output',
                            'strict': response_schema['strict'],
                            'schema': response_schema['schema'],
                        },
                    }
                result = self.responses_client.responses(
                    input=list(self.memory),
                    context=self.context,
                    tools=tools,
                    tool_choice='auto' if tools else 'none',
                    **response_kwargs,
                )
                if not isinstance(result, ResponsesResult):
                    raise TypeError('responses_client must return ResponsesResult')
                return result, result.usage or {}, retries
            except Exception as error:
                last_error = error
                retries.append({
                    'retry': retry,
                    'error': repr(error),
                    'duration_ms': int((time.monotonic() - started_at) * 1000),
                    'token_usage': last_usage,
                })
                if retry < self.completion_max_retries:
                    time.sleep(self.completion_retry_interval)

        statistics = self._statistics(None, last_usage, retries)
        self._trace(
            action_type='error', action_status='failed', step=step,
            result=str(last_error), statistics=statistics,
            record_phase_context=True,
        )
        raise AgentRetryExceededError(str(last_error)) from last_error

    def _append_assistant_result(self, result):
        if result.content:
            self.memory += {'role': 'assistant', 'content': result.content}
        for tool_call in result.tool_calls:
            self.memory += tool_call

    ############# tools operations
    def _register_tools(self, tools):
        for tool in tools or []:
            tool_instance = tool() if inspect.isclass(tool) else tool
            if not isinstance(tool_instance, BasicTool):
                raise TypeError('tools must contain BasicTool instances or classes')
            if tool_instance is tool:
                # A caller-owned instance may retain state from another run.
                tool_instance = type(tool_instance)()

            tool_name = tool_instance.tool.get('name')
            if not tool_name:
                raise ValueError('tool must define a name')
            if tool_name in self._tool_instances:
                raise ValueError(f'duplicate tool name: {tool_name}')
            self._tool_instances[tool_name] = tool_instance

            registration_constants = dict(self.context)
            registration_constants.update({
                'app': self.app,
                'responses_client': self.responses_client,
                'react_tracer': self.react_tracer,
                'context': self.context,
            })
            available_params = inspect.signature(tool_instance.execute).parameters
            tool_instance.register(
                self.tool_executor,
                **{
                    name: value
                    for name, value in registration_constants.items()
                    if name in available_params
                },
            )

    def _execute_tool_calls(self, tool_calls, step, usage, retries):
        for index, tool_call in enumerate(tool_calls):
            self._execute_tool_call(
                tool_call,
                step,
                usage if index == 0 else {},
                retries if index == 0 else [],
            )

    def _execute_tool_call(self, tool_call, step, usage, response_retries):
        tool_name = tool_call.get('name', '')
        trace_id = uuid.uuid4().hex
        started_at = time.monotonic()
        arguments, arguments_error = self._parse_tool_arguments(tool_call)
        if arguments_error:
            return self._record_tool_error(
                tool_call, arguments, arguments_error, step, usage, response_retries,
                started_at,
            )

        self.tool_executor.thought_process.add_tool_calls_group()
        retries = []
        try:
            for retry in range(self.tool_call_max_retries + 1):
                try:
                    raw_result = self.tool_executor.execute(tool_call)
                    observation = self._format_observation(raw_result)
                    tool_token_usage = self._pop_tool_token_usage()
                    statistics = self._statistics(
                        started_at,
                        usage,
                        response_retries + retries,
                        tool_token_usage,
                    )
                    suggestion_targets = self._get_suggestion_targets(raw_result)
                    if suggestion_targets is None:
                        self._trace(
                            action_type='tool_call', action_status='completed', step=step,
                            tool_name=tool_name, tool_arguments=arguments,
                            observation=observation, result=raw_result,
                            statistics=statistics, trace_id=trace_id,
                        )
                    else:
                        for target in suggestion_targets:
                            self._trace(
                                action_type='suggestion', action_status='pending', step=step,
                                tool_name=raw_result.get('trace_tool_name') or tool_name,
                                tool_arguments=arguments,
                                observation=observation, result=raw_result,
                                statistics=statistics, trace_id=trace_id,
                                target_item_type=target['source_type'],
                                target_item_id=target['source_id'],
                                target_item_title=target['source_title'],
                                suggestion_reason=str(raw_result.get('suggestion_reason', '') or '').strip(),
                                suggestion_content=str(raw_result.get('suggestion_content', '') or ''),
                                suggestion_payload=raw_result.get('suggestion_payload') if isinstance(raw_result.get('suggestion_payload'), dict) else {},
                            )
                    self._append_tool_output(tool_call, observation)
                    return True
                except InvalidToolArgumentsException:
                    raise
                except Exception as error:
                    error_details = self._classify_tool_error(error)
                    retries.append({
                        'retry': retry,
                        'reason': 'tool_call_error',
                        'error': repr(error),
                    })
                    if error_details['retryable'] and retry < self.tool_call_max_retries:
                        time.sleep(self.tool_call_retry_interval)
                        continue
                    raise
        except Exception as error:
            tool_token_usage = self._pop_tool_token_usage()
            return self._record_tool_error(
                tool_call, arguments, error, step, usage, response_retries + retries,
                started_at, tool_token_usage,
            )

    def _pop_tool_token_usage(self):
        if not self.tool_executor.thought_process.actions:
            return {}
        return self.tool_executor.thought_process.actions.pop().get('token_usage', {})

    def _get_suggestion_targets(self, result):
        if not isinstance(result, dict) or result.get('status') != 'pending':
            return None
        targets = result.get('trace_targets')
        if not isinstance(targets, list):
            return None
        normalized_targets = []
        for target in targets:
            if not isinstance(target, dict):
                raise TypeError('trace_targets must contain objects')
            source_type = str(target.get('source_type', '') or '').strip()
            source_id = str(target.get('source_id', '') or '').strip()
            if not source_type or not source_id:
                raise ValueError('trace_targets require source_type and source_id')
            normalized_targets.append({
                'source_type': source_type,
                'source_id': source_id,
                'source_title': str(target.get('source_title', '') or '').strip(),
            })
        return normalized_targets

    def _simulate_max_steps_tool_calls(self, tool_calls, step, usage, retries):
        for index, tool_call in enumerate(tool_calls):
            arguments, _ = self._parse_tool_arguments(tool_call)
            error = RuntimeError(self.max_steps_prompt)
            self._record_tool_error(
                tool_call,
                arguments,
                error,
                step,
                usage if index == 0 else {},
                retries if index == 0 else [],
                time.monotonic(),
            )

    @staticmethod
    def _parse_tool_arguments(tool_call):
        raw_arguments = tool_call.get('arguments')
        try:
            arguments = json.loads(raw_arguments) if isinstance(raw_arguments, str) else (raw_arguments or {})
        except (TypeError, json.JSONDecodeError) as error:
            return {}, error
        if not isinstance(arguments, dict):
            return {}, TypeError('Tool arguments must be a JSON object.')
        return arguments, None

    def _record_tool_error(self, tool_call, arguments, error, step, usage, retries, started_at,
                           tool_token_usage=None):
        tool_name = tool_call.get('name', '')
        error_details = self._classify_tool_error(error)
        observation = self._tool_error_observation(
            tool_name,
            error,
            retry_exhausted=bool(retries) and error_details['retryable'],
        )
        self._trace(
            action_type='error', action_status='failed', step=step,
            tool_name=tool_name, tool_arguments=arguments,
            observation=observation, result=str(error),
            statistics=self._statistics(started_at, usage, retries, tool_token_usage),
        )
        self._append_tool_output(tool_call, observation)
        return False

    def _tool_error_observation(self, tool_name, error, *, retry_exhausted=False):
        error_details = self._classify_tool_error(error)
        return json.dumps({
            'status': 'error',
            'tool_name': tool_name,
            'error_type': type(error).__name__,
            'error_code': error_details['code'],
            'retryable': error_details['retryable'],
            'retry_exhausted': retry_exhausted,
            'error_message': str(error),
        }, ensure_ascii=False)

    @staticmethod
    def _classify_tool_error(error):
        """Return stable recovery guidance without exposing exception internals to prompts."""
        if isinstance(error, InvalidToolArgumentsException):
            return {'code': 'invalid_arguments', 'retryable': False}

        message = str(error).lower()
        if 'unsupported source type' in message:
            return {'code': 'unsupported_source_type', 'retryable': False}
        if 'no active' in message and 'record found' in message:
            return {'code': 'not_found', 'retryable': False}
        if 'not an allowed' in message and 'connection' in message:
            return {'code': 'permission_denied', 'retryable': False}
        if 'rate limit' in message or 'too many requests' in message:
            return {'code': 'rate_limited', 'retryable': True}
        if isinstance(error, TimeoutError) or 'timed out' in message or 'timeout' in message:
            return {'code': 'timeout', 'retryable': True}
        if isinstance(error, ConnectionError) or any(
            marker in message
            for marker in ('temporarily unavailable', 'service unavailable', 'connection reset')
        ):
            return {'code': 'transient_failure', 'retryable': True}
        if isinstance(error, ValueError):
            return {'code': 'invalid_input', 'retryable': False}
        return {'code': 'internal_error', 'retryable': False}

    def _append_tool_output(self, tool_call, observation):
        self.memory += {
            'type': 'function_call_output',
            'call_id': tool_call.get('call_id', ''),
            'output': observation,
        }

    ############# final answer operations
    def _validate_final_output(self, content, step, usage, retries):
        if not self.final_output_schema:
            return self._trace_final_output(content, step, usage, retries)

        try:
            output = json.loads(content)
            output = FinalOutputSchemaUtils.validate_and_normalize_output(
                output, self.final_output_schema, AgentFinalOutputSchemaError,
            )
        except (json.JSONDecodeError, AgentFinalOutputSchemaError) as error:
            return self._repair_final_output(content, str(error), step + 1, usage, retries)

        return self._trace_final_output(output, step, usage, retries)

    def _repair_final_output(self, invalid_content, error, step, initial_usage, initial_retries):
        previous_content = invalid_content
        previous_error = error
        for correction_attempt in range(self.completion_max_retries + 1):
            self._trace(
                action_type='error', action_status='failed', step=step + correction_attempt,
                result=previous_error, observation=previous_content,
                statistics=self._statistics(
                    None,
                    initial_usage if correction_attempt == 0 else {},
                    initial_retries if correction_attempt == 0 else [],
                ),
                record_phase_context=True,
            )
            self.memory += {
                'role': 'system',
                'content': (
                    f'Your previous final answer did not match the required JSON schema: {previous_error}. '
                    f'Return only a corrected JSON object.\n{self._schema_prompt()}'
                ),
            }
            result, usage, retries = self._request_response(
                tools=[], step=step + correction_attempt,
            )
            self._append_assistant_result(result)
            if result.tool_calls:
                previous_content = ''
                previous_error = 'Schema correction response must not contain tool calls.'
            else:
                try:
                    output = json.loads(result.content)
                    output = FinalOutputSchemaUtils.validate_and_normalize_output(
                        output, self.final_output_schema, AgentFinalOutputSchemaError,
                    )
                except (json.JSONDecodeError, AgentFinalOutputSchemaError) as schema_error:
                    previous_content = result.content
                    previous_error = str(schema_error)
                else:
                    return self._trace_final_output(
                        output, step + correction_attempt, usage, retries,
                    )

        raise AgentFinalOutputSchemaError(previous_error)

    def _trace_final_output(self, result, step, usage, retries):
        phase = self.phase_spec.phase
        if not phase:
            self._trace(
                action_type='summary', action_status='completed', step=step,
                result=result.get('summary', '') if isinstance(result, dict) else result,
                observation=result,
                statistics=self._statistics(None, usage, retries),
                with_input=True
            )
            return result

        sources = None
        if phase == 'analysis' and isinstance(result, dict):
            analysis_report = result.get('analysis_report')
            if isinstance(analysis_report, str):
                analysis_report, sources = get_answer_and_sources(
                    self.tool_executor, analysis_report,
                )
                result = dict(result)
                result['analysis_report'] = analysis_report
                sources = remove_sources_content_and_snippets(sources)
                result['sources'] = sources
        self._trace(
            action_type=f'{phase}', action_status='completed', step=step,
            result=result, statistics=self._statistics(None, usage, retries),
            references=sources, with_input=True
        )
        return result

    def _trace(self, *, action_type, action_status, step, result=None, tool_name=None,
               tool_arguments=None, observation=None, statistics=None,
                suggestion_reason='', suggestion_content='', suggestion_payload=None,
                target_item_type=None, target_item_id=None, target_item_title=None,
                record_phase_context=False, trace_id=None, references=None, with_input=False):
        if self.react_tracer is None:
            return None
        return self.react_tracer.record(
            context=self.context,
            phase=self.phase_spec.phase,
            record_phase_context=record_phase_context,
            prompt='\n\n'.join(self.system_prompts) if with_input else '',
            input=self.message_input if with_input else '',
            result=result,
            action_type=action_type,
            action_status=action_status,
            step=step,
            tool_name=tool_name,
            tool_arguments=tool_arguments,
            observation=observation,
            statistics=statistics,
            suggestion_reason=suggestion_reason,
            suggestion_content=suggestion_content,
            suggestion_payload=suggestion_payload,
            references=references,
            target_item_type=target_item_type,
            target_item_id=target_item_id,
            target_item_title=target_item_title,
            trace_id=trace_id,
        )

    @staticmethod
    def _format_observation(result):
        if result is None:
            return ''
        if isinstance(result, str):
            return result.strip()
        if isinstance(result, (dict, list)):
            return json.dumps(result, ensure_ascii=False)
        return str(result).strip()

    @staticmethod
    def _statistics(started_at, usage, retries, tool_token_usage=None):
        statistics = dict(usage or {})
        for key in ('input_tokens', 'output_tokens', 'total_tokens'):
            if tool_token_usage and tool_token_usage.get(key):
                statistics[key] = statistics.get(key, 0) + tool_token_usage[key]
        if retries:
            statistics['retries'] = retries
        if started_at is not None:
            statistics['duration_ms'] = int((time.monotonic() - started_at) * 1000)
        return statistics or None

    def _schema_prompt(self):
        return (
            'Your final answer must be a JSON value that conforms exactly to this '
            'JSON Schema. Do not include markdown or explanatory text:\n'
            f'{json.dumps(self.final_output_schema, ensure_ascii=False)}'
        )

    @staticmethod
    def _build_language_instruction(target_language):
        target_language = str(target_language or '').strip()
        if not target_language:
            return ''
        return (
            '<output_language_requirement>\n'
            'Output language requirement: write all natural-language, user-facing '
            f'content in {target_language}. This includes event summaries, analysis '
            'reports, handling reports, replies, messages, and reasons. Keep enum '
            'values, action names, tool names, record identities, citation labels '
            'such as <reference_0>, URLs, and code unchanged.\n'
            '</output_language_requirement>'
        )
