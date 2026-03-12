from seaqa_ai.utils.tools import BasicTool

# import subdir modules for convenience
from seaqa_ai.managers.chat_manager.tools.search_tools import DocumentsSearch, IssuesSearch

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
