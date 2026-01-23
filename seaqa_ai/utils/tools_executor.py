import json
import inspect
import smolagents
from copy import deepcopy
from seaqa_ai.utils.thought_process_recorder import ThoughtProcessRecorder

_tool_type_python_type_map = {
    "string": "str",
    "boolean": "bool",
    "integer": "int",
    "number": "float",
    "array": "list",
    "object": "dict",
    "any": "any",
    "null": "None"
}

class Tool(smolagents.Tool):
    def __init__(self, tool_executor, *args, **kwargs):
        self.tool_executor = tool_executor
        super().__init__(*args, **kwargs)

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
    
class FunctionCallingToolExecutor(BasicToolExecutor):
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

class SmolAgentsToolExecutor(BasicToolExecutor):
    tools = [smolagents.FinalAnswerTool()]

    def _register(self, func, tool_name, tool_description, tool_parms_properties, need_tool_executor, **constant_kwargs):
        parms_str = ''
        call_parms_str = ''
        constant_kwargs_str = ''

        required_parms = {}
        not_required_parms = {}
        for parm_name, parm_info in tool_parms_properties.items():
            if 'default' in parm_info:
                not_required_parms[parm_name] = parm_info
            else:
                required_parms[parm_name] = parm_info
        
        for parm_name, parm_info in required_parms.items():
            if isinstance(parm_info["type"], list):
                parm_type = ' | '.join([_tool_type_python_type_map[tp] for tp in parm_info["type"]])
            else:
                parm_type =  _tool_type_python_type_map[parm_info["type"]]
            parms_str += f', {parm_name}: {parm_type}'
            call_parms_str += f', {parm_name}={parm_name}'
        
        for parm_name, parm_info in not_required_parms.items():
            if isinstance(parm_info["type"], list):
                parm_type = ' | '.join([_tool_type_python_type_map[tp] for tp in parm_info["type"]])
            else:
                parm_type =  _tool_type_python_type_map[parm_info["type"]]

            default_value = json.loads(parm_info["default"])
            if isinstance(default_value, str):
                default_value = '"""' + default_value + '"""'

            parms_str += f', {parm_name}: {parm_type}={default_value}'
            call_parms_str += f', {parm_name}={parm_name}'

        for kw_name, kw_value in constant_kwargs.items():
            constant_kwargs_str += f', {kw_name}={kw_value if not isinstance(kw_value, str) else "\"\"\"" + kw_value + "\"\"\""}'

        tool_executor_str = ''
        if need_tool_executor:
            tool_executor_str += ', tool_executor=self.tool_executor'

        all_parms_str = f'{call_parms_str}{constant_kwargs_str}{tool_executor_str}'
        if ', ' == all_parms_str[:2]:
            all_parms_str = all_parms_str[2:]

        register_string = f"""
class tool_{tool_name}(Tool):
    inputs: dict = {tool_parms_properties}

    output_type: str = 'string'

    def forward(self{parms_str}) -> str:
        return self.tool_func({all_parms_str})
"""
        namespace = {}
        exec(register_string, globals(), namespace)
        tool_class = namespace[f"tool_{tool_name}"]
        tool_class.name = tool_name
        tool_class.description = tool_description
        tool_class.tool_func = func
        return tool_class(self)
        
    def _register_tool(self, func, tool_info, **constant_kwargs):
        """
        convert and register a json-like tool to smolagents' tool:

        A json-like tool is defined as:

        {
            'type': 'function',
            'function': {
                'name': 'tool\'s name',
                'description': 'tool\' description',
                'parameters':{
                    'type': 'object',
                    'properties': {
                        'par_name': {
                            'type': 'string',
                            'default': 'null', // if is not required
                            'description': 'par\'s description'
                        }
                        // ...
                    }
                    'required': ['par_name'] // only when 'default' is not set
                }
            }
        }
        """

        tool_info = deepcopy(tool_info['function'])
        tool_name = tool_info['name']
        tool_description = tool_info['description']
        availables_parms = inspect.signature(func).parameters
        if tool_parms := tool_info.get('parameters', {}):
            tool_parms_properties = tool_parms.get('properties', {})
            tool_parms_required = tool_parms.get('required', [])
            for not_required_parm in tool_parms_properties.keys():
                if not_required_parm in tool_parms_required:
                    continue
                if 'default' not in tool_parms_properties[not_required_parm]:
                    tool_parms_properties[not_required_parm]['default'] = 'null'
                    if not isinstance(tool_parms_properties[not_required_parm]['type'], list):
                        tool_parms_properties[not_required_parm]['type'] = [tool_parms_properties[not_required_parm]['type']]
                    tool_parms_properties[not_required_parm]['type'].append('null')
                    tool_parms_properties[not_required_parm]['nullable'] = True
        else:
            tool_parms_properties = {}

        constant_kwargs_after_filter = {
            k: v
            for k, v in constant_kwargs.items() if k in availables_parms and k not in tool_parms_properties
        }

        # check args
        supply_param_names = []
        for par_name in tool_parms_properties:
            supply_param_names.append(par_name)
        for par_name in constant_kwargs_after_filter:
            supply_param_names.append(par_name)
        if 'tool_executor' not in supply_param_names and 'tool_executor' in availables_parms:
            need_tool_executor = True
            supply_param_names.append('tool_executor')
        else:
            need_tool_executor = False
        
        assert set(supply_param_names) == set(availables_parms), f'Failure to register tool, supply: {supply_param_names}, available: {availables_parms}'

        return self._register(func, tool_name, tool_description, tool_parms_properties, need_tool_executor, **constant_kwargs_after_filter)

    def clean(self):
        self.tools = [smolagents.FinalAnswerTool()]

    def register(self, func, tool_info, **constant_kwargs):
        self.tools.append(self._register_tool(func, tool_info, **constant_kwargs))
