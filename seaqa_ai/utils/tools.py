import smolagents

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


class SmolAgentsToolWrapper(smolagents.Tool):
    """
    Wrapper that converts an OpenAI-style tool definition to a smolagents Tool.
    
    This allows existing tools (defined with OpenAI function calling format)
    to be used with smolagents agents.
    """
    
    def __init__(self, func, tool_info, **constant_kwargs):
        """
        Initialize the wrapper.
        
        Args:
            func: The callable function to execute
            tool_info: OpenAI-style tool definition dict
            constant_kwargs: Kwargs to always pass to the function
        """
        self._func = func
        self._constant_kwargs = constant_kwargs
        
        function_spec = tool_info.get('function', {})
        self.name = function_spec.get('name', 'unknown')
        self.description = function_spec.get('description', '')
        
        # Convert OpenAI parameters to smolagents inputs format
        params = function_spec.get('parameters', {})
        properties = params.get('properties', {})
        required = params.get('required', [])
        
        self.inputs = {}
        for param_name, param_spec in properties.items():
            openai_type = param_spec.get('type', 'string')
            # Map OpenAI types to smolagents types
            type_mapping = {
                'string': 'string',
                'integer': 'integer',
                'number': 'number',
                'boolean': 'boolean',
                'array': 'array',
                'object': 'object',
            }
            smolagents_type = type_mapping.get(openai_type, 'string')
            
            self.inputs[param_name] = {
                'type': smolagents_type,
                'description': param_spec.get('description', ''),
            }
            if param_name not in required:
                self.inputs[param_name]['nullable'] = True
        
        self.output_type = 'string'
        super().__init__()
    
    def validate_arguments(self):
        # Skip parameter signature validation since we use **kwargs
        # to dynamically accept all inputs defined in self.inputs
        pass
    
    def forward(self, **kwargs):
        """Execute the wrapped function."""
        # Merge constant kwargs (but don't override explicit kwargs)
        merged_kwargs = {**self._constant_kwargs}
        merged_kwargs.update(kwargs)
        return self._func(**merged_kwargs)


class SmolAgentsToolExecutor(BasicToolExecutor):
    """
    Tool executor that wraps tools for smolagents compatibility.
    
    This executor registers tools and converts them to smolagents Tool format,
    allowing them to be used with smolagents.ToolCallingAgent.
    """
    
    def __init__(self):
        self._tools = {}
        super().__init__()
    
    def clean(self):
        """Clear all registered tools."""
        self._tools = {}
    
    @property
    def tools(self):
        """
        Return tools as a list for smolagents agent initialization.
        
        smolagents.ToolCallingAgent expects a list of Tool objects,
        which it converts internally to a dict keyed by tool name.
        """
        return list(self._tools.values())
    
    def register(self, func, tool_info, **constant_kwargs):
        """
        Register a tool by wrapping it in SmolAgentsToolWrapper.
        
        Args:
            func: The callable function to execute
            tool_info: OpenAI-style tool definition dict
            constant_kwargs: Kwargs to always pass to the function
        """
        name = tool_info.get('function', {}).get('name')
        if not name:
            raise ValueError(f'Invalid tool_info: missing function name in {tool_info}')
        
        wrapper = SmolAgentsToolWrapper(func, tool_info, **constant_kwargs)
        self._tools[name] = wrapper
    
    def get(self):
        """
        Return tool definitions in OpenAI format.
        
        This is for compatibility with code that expects OpenAI-style tool info.
        """
        return [
            {
                'type': 'function',
                'function': {
                    'name': tool.name,
                    'description': tool.description,
                    'parameters': {
                        'type': 'object',
                        'properties': {
                            name: {
                                'type': spec.get('type', 'string'),
                                'description': spec.get('description', ''),
                            }
                            for name, spec in tool.inputs.items()
                        },
                        'required': [
                            name for name, spec in tool.inputs.items()
                            if not spec.get('nullable', False)
                        ]
                    }
                }
            }
            for tool in self._tools.values()
        ]
