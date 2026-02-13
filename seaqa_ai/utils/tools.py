from seaqa_ai.utils.thought_process_recorder import ThoughtProcessRecorder

class BasicTool(object):
    tool = {}
    def __init__(self, app):
        self.app = app

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
