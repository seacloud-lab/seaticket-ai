import logging
import yaml
import importlib

from smolagents import ToolCallingAgent
from smolagents.monitoring import LogLevel


logger = logging.getLogger(__name__)


class AgentManager:
    def __init__(self, app):
        self.app = app

    def execute(self, agent_name, message, **kwargs):
        if agent_name == 'tool_call_agent':
            retriever_tool = kwargs.get('retriever_tool')
            server_model = kwargs.get('server_model')
            agent = ToolCallingAgent(
                prompt_templates=yaml.safe_load(
                    importlib.resources.files("seaqa_ai.prompts").joinpath("toolcalling_agent.yaml").read_text()
                ),
                return_full_result=True,
                tools=[retriever_tool],  # List of tools available to the agent
                model=server_model,
                max_steps=3,  # Limit the number of reasoning steps
                verbosity_level=LogLevel.OFF,  # Show detailed agent reasoning debug
            )
            agent_output = agent.run(message)

            return agent_output
