import json
import inspect
from seaqa_ai.utils.tools import BasicTool, BasicToolExecutor, InvalidToolException

# import subdir modules for convenience
from seaqa_ai.managers.chat_manager.tools.search_tools import DocumentsSearch, IssuesSearch
from seaqa_ai.managers.chat_manager.tools.content_generators import GenerateMarkdown

class TestTool(BasicTool):
    tool = {
        'type': 'function',
        'function': {
            'name': 'test_tool',
            'description': "This tool is used to test whether the Tool Call functionality is working properly.",
            'parameters':{
                'type': 'object',
                'properties': {
                    'paramters': {
                        'type': 'string',
                        'description': 'Parameters used in the test'
                    },
                    'error': {
                        'type': 'string',
                        'description': 'Providing this parameter will raise a related error'
                    }
                },
                'required': []
            }
        }
    }

    def execute(self, paramters=None, error=None):
        if error:
            raise Exception(str(error))
        return f'Test tool executes successfully. (Received parameters: {paramters})'

class ToolExecutor(BasicToolExecutor):
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
    
    def execute(self, tool_call):
        function = tool_call['function']
        name = function['name']
        params = json.loads(function.get('arguments', '{}'))
        constant_kwargs = self.tools[name]['constant_kwargs']
        avaliables_params = inspect.signature(self.tools[name]['func']).parameters
        params.update({
            k: v
            for k, v in constant_kwargs.items() if k in avaliables_params and k not in params
        })

        if 'tool_executor' in avaliables_params and 'tool_executor' not in params:
            params.update({'tool_executor': self})
        
        return self.tools[name]['func'](**params)

