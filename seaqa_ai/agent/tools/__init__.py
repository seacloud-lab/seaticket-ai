class BasicTool(object):
    tool = {}
    def __init__(self, app, ):
        self.app = app

    def register(self, tool_executor, **constant_kwargs):
        tool_executor.register(self.execute, self.tool, **constant_kwargs)

    def execute(self, *args, **kwargs):
        raise NotImplementedError()

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
