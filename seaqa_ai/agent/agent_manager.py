import logging
import yaml
import importlib
import json

from smolagents import ToolCallingAgent
from smolagents.monitoring import LogLevel


logger = logging.getLogger(__name__)


class AgentManager:
    def __init__(self, app):
        self.app = app

    def execute(self, agent_name, message, **kwargs):
        if agent_name == 'tool_call_agent':
            tools = kwargs.get('tools', [])
            server_model = kwargs.get('server_model')
            agent = ToolCallingAgent(
                prompt_templates=yaml.safe_load(
                    importlib.resources.files("seaqa_ai.prompts").joinpath("toolcalling_agent.yaml").read_text()
                ),
                tools=tools,  # List of tools available to the agent
                model=server_model,
                max_steps=3,  # Limit the number of reasoning steps
                verbosity_level=LogLevel.OFF,  # Show detailed agent reasoning debug
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
            sources = json.loads(sources) if isinstance(sources, str) else sources
            return output.get('answer', ''), agent_output.messages, sources

