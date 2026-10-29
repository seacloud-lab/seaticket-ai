import inspect
import json
from abc import abstractmethod
from copy import deepcopy
from difflib import get_close_matches
from jsonschema import Draft202012Validator
from jsonschema.exceptions import SchemaError
from seaqa_ai.config import THOUGHT_PROCESS_ENABLED, THOUGHT_PROCESS_RECORD_TOOL_DETAILS
from seaqa_ai.utils import remove_sources_content_and_snippets
from seaqa_ai.utils.thought_process_recorder import ThoughtProcessRecorder

SEARCH_TOOL_NAMES = (
    'documents_search',
    'issues_search',
    'tasks_search',
)

def filter_tool_calls_content(tool_name, results):
    if not THOUGHT_PROCESS_RECORD_TOOL_DETAILS and tool_name in (*SEARCH_TOOL_NAMES, 'get_records'):
        return remove_sources_content_and_snippets(deepcopy(results))
    return results

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
        self.thought_process = ThoughtProcessRecorder(enabled=THOUGHT_PROCESS_ENABLED)
        self.clean()

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

    def execute(self, tool_call, call_back=lambda: None):
        name = tool_call['name']
        raw_arguments = tool_call.get('arguments')
        if isinstance(raw_arguments, str):
            params = json.loads(raw_arguments or '{}')
        else:
            params = raw_arguments or {}
        if not isinstance(params, dict):
            raise InvalidToolException(tool_call)
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
