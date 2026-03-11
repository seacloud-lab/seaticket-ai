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


class SimpleToolExecutor(BasicToolExecutor):
    """
    Tool executor that stores tools in OpenAI function calling format.
    """

    def __init__(self):
        self._tools = {}
        super().__init__()

    def clean(self):
        """Clear all registered tools."""
        self._tools = {}

    def register(self, func, tool_info, **constant_kwargs):
        """
        Register a tool.

        Args:
            func: The callable function to execute
            tool_info: OpenAI-style tool definition dict
            constant_kwargs: Kwargs to always pass to the function
        """
        name = tool_info.get('function', {}).get('name')
        if not name:
            raise ValueError(f'Invalid tool_info: missing function name in {tool_info}')
        self._tools[name] = (func, tool_info, constant_kwargs)

    def execute(self, name, **kwargs):
        """Execute a registered tool by name."""
        if name not in self._tools:
            raise ValueError(f'Unknown tool: {name}')
        func, _, constant_kwargs = self._tools[name]
        merged = {**constant_kwargs, **kwargs}
        return func(**merged)

    def get(self):
        """Return tool definitions in OpenAI format."""
        return [tool_info for _, tool_info, _ in self._tools.values()]
