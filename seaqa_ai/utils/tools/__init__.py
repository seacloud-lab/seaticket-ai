import json
import inspect
from abc import abstractmethod
from copy import deepcopy
from seaqa_ai.config import THOUGHT_PROCESS_ENABLED, THOUGHT_PROCESS_RECORD_TOOL_DETAILS
from seaqa_ai.utils import remove_sources_content_and_snippets
from seaqa_ai.utils.thought_process_recorder import ThoughtProcessRecorder

SEARCH_TOOL_NAMES = (
    'documents_search',
    'issues_search'
)

def filter_tool_calls_content(tool_name, results):
    if not THOUGHT_PROCESS_RECORD_TOOL_DETAILS and tool_name in SEARCH_TOOL_NAMES:
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

from .search_tools import DocumentsSearch, IssuesSearch
from .test_tool import TestTool

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
    
    def execute(self, tool_call, call_back=lambda: None):
        name = tool_call['name']
        raw_arguments = tool_call.get('arguments')
        if isinstance(raw_arguments, str):
            params = json.loads(raw_arguments or '{}')
        else:
            params = raw_arguments or {}
        if not isinstance(params, dict):
            raise InvalidToolException(tool_call)
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
