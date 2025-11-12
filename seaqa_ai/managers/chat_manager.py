import logging
from seaqa_ai.config import LLM_MODEL
from seaqa_ai.agent.tools import SearchTools
from seaqa_ai.utils.tools_executor import FunctionCallingToolExecutor, SmolAgentsToolExecutor
from seaqa_ai.utils.constants import ASK_MODE_PROPERTIES_MAP, AGENT_MODE_PROPERTIES_MAP
from smolagents.models import LiteLLMModel

logger = logging.getLogger(__name__)

class ChatManager:
    def __init__(self, app):
        self.app = app
        self.search_tools = SearchTools(app)

    def get_ai_reply(self, resolve_type, message, project_uuid, connection_ids, context, model=None):
        if resolve_type == AGENT_MODE_PROPERTIES_MAP.RESOLVE_TYPE.value:
            return self._gen_ans_agent(message, project_uuid, connection_ids, context, model)
        elif resolve_type == ASK_MODE_PROPERTIES_MAP.RESOLVE_TYPE.value:
            return self._gen_ans_ask(message, project_uuid, connection_ids, context, model)
        else:
            return f"Invalid resolve type"

    def _gen_ans_ask(self, message, project_uuid, connection_ids, context, model=None):
        """
        Generate an answer based on the question and references.
        """
        tool_executor = FunctionCallingToolExecutor()
        self.search_tools.registers(tool_executor, project_uuid=project_uuid, connection_ids=connection_ids)

        model_to_use = model if model else LLM_MODEL
        temperature = 1 if 'gpt-5' in model_to_use else 0.1

        try:
            return self.app.agent_manager.execute(ASK_MODE_PROPERTIES_MAP.EXECUTE_AGENT.value, tool_executor, message, context=context, temperature=temperature, model=model_to_use)

        except Exception as e:
            logger.exception(f"Error generating answer: {str(e)}")
            return f"Sorry, an error occurred while generating the answer: {str(e)}"

    def _gen_ans_agent(self, message, project_uuid, connection_ids, context, model=None):
        tool_executor = SmolAgentsToolExecutor()
        self.search_tools.registers(tool_executor, project_uuid=project_uuid, connection_ids=connection_ids)
        model_to_use = model if model else LLM_MODEL

        if 'gpt-5' not in model_to_use:
            server_model = LiteLLMModel(client=self.app.llm_api, model_id=model_to_use, context=context)
        else:
            server_model = LiteLLMModel(client=self.app.llm_api, model_id=model_to_use, context=context, temperature=1)

        return self.app.agent_manager.execute(AGENT_MODE_PROPERTIES_MAP.EXECUTE_AGENT.value, tool_executor, message, server_model=server_model, context=context)
