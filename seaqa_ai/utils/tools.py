import json
import inspect
from copy import deepcopy
from seaqa_ai.config import THOUGHT_PROCESS_RECORD_TOOL_DETAILS
from seaqa_ai.utils import remove_sources_content_and_snippets
from seaqa_ai.utils.thought_process_recorder import ThoughtProcessRecorder

def filter_tool_calls_content(tool_name, results):
    if not THOUGHT_PROCESS_RECORD_TOOL_DETAILS and tool_name in (
            'documents_search', 
            'issues_search'
        ):
        return remove_sources_content_and_snippets(deepcopy(results))
    return results

class BasicTool(object):
    tool = {}

    def register(self, tool_executor, **constant_kwargs):
        tool_executor.register(self.execute, self.tool, **constant_kwargs)

    def execute(self, *args, **kwargs):
        raise NotImplementedError()
    
class InvalidToolException(Exception):
    pass

class BasicToolExecutor(object):
    def __init__(self):
        self.cache = {}
        self.thought_process = ThoughtProcessRecorder()
        self.clean()

    def register(self, func, tool_info, **constant_kwargs):
        raise NotImplementedError()
    
    def registers(self, funcs, tool_infos, constant_kwargs_s=[]):
        if len(funcs) != len(tool_infos):
            raise InvalidToolException('Register tools set failure, length does not match!')

        for i in range(len(funcs)):
            try:
                constant_kwargs = constant_kwargs_s[i]
            except IndexError:
                constant_kwargs = {}
            self.register(funcs[i], tool_infos[i], **constant_kwargs)
    
    def clean(self, *args, **kwargs):
        raise NotImplementedError()

class OpenAIToolExecutor(BasicToolExecutor):
    tools = {}
    
    def clean(self):
        self.tools = {}

    def register(self, func, tool_info, **constant_kwargs):
        """
        func: callable
        tool_info: json-like openai format tool defined
        """
        name = tool_info.get('function', {}).get('name')
        if not name:
            raise InvalidToolException(tool_info)
        
        self.tools[name] = {
            'func': func,
            'constant_kwargs': constant_kwargs,
            'tool_info': tool_info
        }

    def get(self):
        return [
            tool['tool_info']
            for tool in self.tools.values()
        ]
    
    def execute(self, tool_call, call_back=lambda: None):
        function = tool_call['function']
        name = function['name']
        params = json.loads(function.get('arguments', '{}'))
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
