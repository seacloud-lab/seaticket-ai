class BasicTools(object):
    tools = {}
    def __init__(self, app):
        self.app = app
        self._initialize_tool_name_func_map()
        assert isinstance(self.tool_name_func_map, dict), "Property tool_name_func_map must be a dict after initializing"

    def _initial_tool_name_func_map(self):
        raise NotImplementedError()
    
    def register(self, tool_executor, tool_name, **constant_kwargs):
        if tool_name in self.tool_name_func_map:
            tool_executor.register(self.tool_name_func_map[tool_name], tool_name, **constant_kwargs)
    
    def registers(self, tool_executor, **constant_kwargs):
        for tool_name, tool_func in self.tool_name_func_map.items():
            tool_executor.register(tool_func, self.tools[tool_name], **constant_kwargs)

    @classmethod
    def get_tools(cls):
        return cls.tools
