class BasicTool(object):
    tool = {}
    def __init__(self, app, ):
        self.app = app

    def register(self, tool_executor, **constant_kwargs):
        tool_executor.register(self.execute, self.tool, **constant_kwargs)

    def execute(self, *args, **kwargs):
        raise NotImplementedError()
