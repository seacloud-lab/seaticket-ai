from seaqa_ai.utils.tools import BasicTool

class TestTool(BasicTool):
    tool = {
        'type': 'function',
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
        },
        'strict': False,
    }

    def execute(self, paramters=None, error=None):
        if error:
            raise Exception(str(error))
        return f'Test tool executes successfully. (Received parameters: {paramters})'
