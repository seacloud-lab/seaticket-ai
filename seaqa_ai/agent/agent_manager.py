import logging
import yaml
import importlib
import json

from smolagents import ToolCallingAgent
from smolagents.monitoring import LogLevel
from seaqa_ai.prompts.ask import system_prompts


logger = logging.getLogger(__name__)


class AgentManager:
    def __init__(self, app):
        self.app = app

    def execute(self, agent_name, message, **kwargs):
        # agent mode
        if agent_name == 'reasoning_agent':
            tool_executor = kwargs.get('tool_executor')
            server_model = kwargs.get('server_model')
            agent = ToolCallingAgent(
                prompt_templates=yaml.safe_load(
                    importlib.resources.files("seaqa_ai.prompts").joinpath("toolcalling_agent.yaml").read_text()
                ),
                tools=tool_executor.tools if tool_executor else [],  # List of tools available to the agent
                model=server_model,
                max_steps=3,  # Limit the number of reasoning steps
                verbosity_level=LogLevel.DEBUG,  # Show detailed agent reasoning debug
                return_full_result=True,
            )
            agent_output = agent.run(message)
            for msg in agent_output.messages:
                model_input_messages = msg.get('model_input_messages')
                if model_input_messages:
                    msg['model_input_messages'] = [input_msg.dict() for input_msg in msg.get('model_input_messages')]

                model_output_message = msg.get('model_output_message')
                if model_output_message:
                    model_output_message.pop('raw', None)

                model_output = msg.get('model_output')
                if model_output:
                    msg['model_output'] = [model_output_msg.dict() for model_output_msg in model_output]

            output = agent_output.output
            sources = output.get('sources', [])
            try:
                sources = json.loads(sources) if isinstance(sources, str) else sources
            except:
                sources = []
            return output.get('answer', ''), agent_output.messages, sources

        # ask mode
        elif agent_name == 'function_calling':
            tool_executor=kwargs.get('tool_executor')
            temperature=kwargs.get('temperature', 0.1)
            context=kwargs.get('context')
            messages = [
                {
                    'role': 'system',
                    'content': system_prompts
                },
                {
                    'role': 'user',
                    'content': message
                }
            ]
            response = self.app.llm_api.completion(
                messages=messages,
                context=context,
                tools=tool_executor.get() if tool_executor else [],
                temperature=temperature
            ).choices[0].message

            if response.tool_calls:

                tool_call = response.tool_calls[0]

                messages.append({
                    'role': 'assistant',
                    'tool_calls': response.tool_calls
                })

                messages.append({
                    'role': 'tool',
                    'tool_call_id': tool_call['id'],
                    'content': json.dumps(tool_executor.execute(tool_call))
                })

                content = self.app.llm_api.completion(
                        messages=messages,
                        context=context,
                        temperature=temperature,
                    ).choices[0].message.content
            else:
                content = response.content

            try:
                content = json.loads(content)
                return content['answer'], content['sources']
            except Exception as e:
                logger.warning(f"Failure to parse json: {str(e)}")
                return content, []
