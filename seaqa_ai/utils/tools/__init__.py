import inspect
import json
import logging
import time
from abc import abstractmethod
from copy import deepcopy
from difflib import get_close_matches
from jsonschema import Draft202012Validator
from jsonschema.exceptions import SchemaError
from seaqa_ai.config import THOUGHT_PROCESS_ENABLED, THOUGHT_PROCESS_RECORD_TOOL_DETAILS
from seaqa_ai.utils import remove_sources_content_and_snippets
from seaqa_ai.utils.llm_api.usage import sum_credit_used
from seaqa_ai.utils.thought_process_recorder import ThoughtProcessRecorder

logger = logging.getLogger(__name__)

SEARCH_TOOL_NAMES = (
    'documents_search',
    'issues_search',
    'tasks_search',
)

TOOL_LOG_ARGUMENTS_MAX_CHARS = 300

def filter_tool_calls_content(tool_name, results):
    if not THOUGHT_PROCESS_RECORD_TOOL_DETAILS and tool_name in (*SEARCH_TOOL_NAMES, 'get_records'):
        return remove_sources_content_and_snippets(deepcopy(results))
    return results

def _record_key(source_type, connection_id, record_id):
    return ':'.join(str(part) for part in (source_type, connection_id, record_id) if part is not None)

def summarize_tool_result(tool_name, result):
    """Tool-specific counters for the `Tool call done` log line."""
    if not isinstance(result, list):
        return {}
    items = [item for item in result if isinstance(item, dict)]
    if tool_name in SEARCH_TOOL_NAMES:
        docs = [item for item in items if 'label' in item]
        return {
            'results': len(docs),
            'duplicates': sum('notice' in item for item in items),
            'snippets': sum(len(doc.get('snippets') or []) for doc in docs),
            'records': ','.join(
                _record_key(doc.get('type'), doc.get('connection_id'), doc.get('record_id')) for doc in docs
            ) or None,
        }
    if tool_name == 'get_records':
        read = [item for item in items if 'notice' not in item and 'error' not in item]
        notices = [item for item in items if 'notice' in item]
        return {
            'targets': len(result),
            'read': len(read),
            'duplicates': sum('reference_id' in item for item in notices),
            'limited': sum('reference_id' not in item for item in notices),
            'errors': sum('error' in item for item in items),
            'records': ','.join(
                _record_key(item.get('source_type'), item.get('connection_id'), item.get('record_id')) for item in read
            ) or None,
        }
    return {}

def _tool_log_fields(tool_name, context, log_fields):
    context = context or {}
    return {
        'tool': tool_name,
        'scenario': context.get('scenario'),
        'project_uuid': context.get('project_uuid'),
        'session_uuid': context.get('session_uuid'),
        'run_id': context.get('run_id'),
        **(log_fields or {}),
    }

def _format_log_fields(fields):
    return ' '.join(f'{key}={value}' for key, value in fields.items() if value is not None)

def _format_tool_arguments(arguments):
    if not isinstance(arguments, str):
        arguments = json.dumps(arguments, ensure_ascii=False, default=str)
    arguments = arguments.replace('\n', ' ')
    if len(arguments) > TOOL_LOG_ARGUMENTS_MAX_CHARS:
        return arguments[:TOOL_LOG_ARGUMENTS_MAX_CHARS] + '...'
    return arguments

def log_tool_call_done(tool_name, arguments, result, context, started_at, log_fields=None):
    # Logging must never break a tool call whose result is already available.
    try:
        fields = _tool_log_fields(tool_name, context, log_fields)
        fields.update(summarize_tool_result(tool_name, result))
        fields['output_chars'] = len(json.dumps(result, ensure_ascii=False, default=str))
        fields['duration_ms'] = int((time.monotonic() - started_at) * 1000)
        logger.info('Tool call done: %s args=%s', _format_log_fields(fields), _format_tool_arguments(arguments))
    except Exception as error:
        logger.warning('Failure to log tool call %s: %r', tool_name, error)

def log_tool_call_failed(tool_name, arguments, error, context, started_at, log_fields=None):
    fields = _tool_log_fields(tool_name, context, log_fields)
    fields['duration_ms'] = int((time.monotonic() - started_at) * 1000)
    logger.warning(
        'Tool call failed: %s error=%r args=%s',
        _format_log_fields(fields), error, _format_tool_arguments(arguments),
    )

def log_tool_call_skipped(tool_name, arguments, reason, context, log_fields=None):
    fields = _tool_log_fields(tool_name, context, log_fields)
    fields['reason'] = reason
    logger.warning('Tool call skipped: %s args=%s', _format_log_fields(fields), _format_tool_arguments(arguments))

class BasicTool(object):
    tool = {}

    def register(self, tool_executor, **constant_kwargs):
        tool_executor.register(self.execute, self.tool, **constant_kwargs)

    @abstractmethod
    def execute(self, *args, **kwargs):
        raise NotImplementedError(args, kwargs)
    
class InvalidToolException(Exception):
    pass


class InvalidToolArgumentsException(InvalidToolException):
    """Raised when model-provided arguments do not match a tool's schema."""

    def __init__(
        self,
        tool_name,
        missing_fields=None,
        unexpected_fields=None,
        suggestions=None,
        schema_error=None,
    ):
        self.tool_name = tool_name
        self.missing_fields = missing_fields or []
        self.unexpected_fields = unexpected_fields or []
        self.suggestions = suggestions or {}
        self.schema_error = schema_error

        details = []
        if self.schema_error:
            details.append(self.schema_error)
        if self.missing_fields:
            details.append(f"missing required fields: {', '.join(self.missing_fields)}")
        if self.unexpected_fields:
            unexpected = []
            for field in self.unexpected_fields:
                suggestion = self.suggestions.get(field)
                unexpected.append(f"{field} (did you mean {suggestion}?)" if suggestion else field)
            details.append(f"unexpected fields: {', '.join(unexpected)}")

        message = f"Invalid arguments for tool {tool_name!r}: {'; '.join(details)}. "
        message += 'Call the tool again with arguments that conform to the registered JSON Schema.'
        super().__init__(message)


from .search_tools import DocumentsSearch, IssuesSearch, TasksSearch
from .test_tool import TestTool
from .get_records import GetRecords

class BasicToolExecutor(object):
    def __init__(self):
        self.cache = {}
        self._token_usage = {}
        self.thought_process = ThoughtProcessRecorder(enabled=THOUGHT_PROCESS_ENABLED)
        self.clean()

    def record_token_usage(self, token_usage):
        credit_used = sum_credit_used(self._token_usage, token_usage)
        for key in ('input_tokens', 'cached_tokens', 'output_tokens', 'total_tokens'):
            self._token_usage[key] = self._token_usage.get(key, 0) + token_usage.get(key, 0)
        self._token_usage['credit_used'] = credit_used
        self.thought_process.update_last_group_tokens_usage(token_usage)

    def pop_token_usage(self):
        token_usage = self._token_usage
        self._token_usage = {}
        return token_usage

    @abstractmethod
    def register(self, func, tool_info, **constant_kwargs):
        raise NotImplementedError(func, tool_info, constant_kwargs)
    
    def registers(self, funcs, tool_infos, constant_kwargs_s=[]):
        if len(funcs) != len(tool_infos):
            raise InvalidToolException('Register tools set failure, length does not match!')

        for i in range(len(funcs)):
            try:
                constant_kwargs = constant_kwargs_s[i]
            except IndexError:
                constant_kwargs = {}
            self.register(funcs[i], tool_infos[i], **constant_kwargs)
    
    @abstractmethod
    def clean(self, *args, **kwargs):
        raise NotImplementedError(args, kwargs)

class OpenAIToolExecutor(BasicToolExecutor):
    tools = {}
    
    def clean(self):
        self.tools = {}

    def register(self, func, tool_info, **constant_kwargs):
        """
        func: callable
        tool_info: Responses function tool definition
        """
        name = tool_info.get('name')
        if not name:
            raise InvalidToolException(tool_info)
        tool_info = dict(tool_info)
        if tool_info.get('type') == 'function':
            tool_info.setdefault('strict', False)

        self.tools[name] = {
            'func': func,
            'constant_kwargs': constant_kwargs,
            'tool_info': tool_info,
        }

    def get(self):
        return [
            tool['tool_info']
            for tool in self.tools.values()
        ]

    def validate_arguments(self, name, params):
        """Validate model arguments against the complete registered JSON Schema."""
        parameters = self.tools[name]['tool_info'].get('parameters') or {}
        if name == 'get_records':
            # Per-target validation belongs to the tool so one malformed identity
            # does not prevent valid targets in the same batch from being read.
            parameters = deepcopy(parameters)
            parameters['properties']['targets']['items'] = {}
        properties = parameters.get('properties')
        if isinstance(properties, dict):
            valid_fields = set(properties)
            required_fields = parameters.get('required') or []
            missing_fields = sorted(field for field in required_fields if field not in params)
            unexpected_fields = sorted(field for field in params if field not in valid_fields)
            if missing_fields or unexpected_fields:
                suggestions = {}
                for field in unexpected_fields:
                    matches = get_close_matches(field, sorted(valid_fields), n=1, cutoff=0.75)
                    if matches:
                        suggestions[field] = matches[0]
                raise InvalidToolArgumentsException(
                    name,
                    missing_fields=missing_fields,
                    unexpected_fields=unexpected_fields,
                    suggestions=suggestions,
                )

        try:
            validator = Draft202012Validator(parameters)
        except SchemaError as error:
            raise InvalidToolException(
                f'Invalid schema registered for tool {name!r}: {error.message}'
            ) from error

        error = next(validator.iter_errors(params), None)
        if error is None:
            return

        path = '$'
        for component in error.absolute_path:
            path += f'[{component}]' if isinstance(component, int) else f'.{component}'

        raise InvalidToolArgumentsException(
            name,
            schema_error=f'{path}: {error.message}',
        )

    def tool_context(self, name):
        return (self.tools.get(name) or {}).get('constant_kwargs', {}).get('context')

    def log_skipped(self, tool_call, reason, log_fields=None):
        name = tool_call.get('name', '')
        log_tool_call_skipped(name, tool_call.get('arguments'), reason, self.tool_context(name), log_fields)

    def execute(self, tool_call, call_back=lambda: None, log_fields=None):
        name = tool_call['name']
        raw_arguments = tool_call.get('arguments')
        started_at = time.monotonic()
        try:
            result = self._execute(name, raw_arguments, call_back)
        except Exception as error:
            log_tool_call_failed(name, raw_arguments, error, self.tool_context(name), started_at, log_fields)
            raise
        log_tool_call_done(name, raw_arguments, result, self.tool_context(name), started_at, log_fields)
        return result

    def _execute(self, name, raw_arguments, call_back):
        if isinstance(raw_arguments, str):
            params = json.loads(raw_arguments or '{}')
        else:
            params = raw_arguments or {}
            if isinstance(params, dict):
                params = dict(params)
        if not isinstance(params, dict):
            raise InvalidToolException({'name': name, 'arguments': raw_arguments})
        self.validate_arguments(name, params)

        constant_kwargs = self.tools[name]['constant_kwargs']
        available_params = inspect.signature(self.tools[name]['func']).parameters
        params.update({
            k: v
            for k, v in constant_kwargs.items() if k in available_params and k not in params
        })

        if 'tool_executor' in available_params and 'tool_executor' not in params:
            params.update({'tool_executor': self})
        
        if 'call_back' in available_params and 'call_back' not in params:
            params.update({'call_back': call_back})

        return self.tools[name]['func'](**params)
