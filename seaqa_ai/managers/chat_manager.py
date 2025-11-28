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

    def get_ai_reply(self, resolve_type, message, project_uuid, connection_ids, context):
        if resolve_type == AGENT_MODE_PROPERTIES_MAP.RESOLVE_TYPE.value:
            return self._gen_ans_agent(message, project_uuid, connection_ids, context)
        elif resolve_type == ASK_MODE_PROPERTIES_MAP.RESOLVE_TYPE.value:
            return self._gen_ans_ask(message, project_uuid, connection_ids, context)
        else:
            return f"Invalid resolve type"

    def _gen_ans_ask(self, message, project_uuid, connection_ids, context):
        """
        Generate an answer based on the question and references.
        """
        tool_executor = FunctionCallingToolExecutor()
        self.search_tools.registers(tool_executor, project_uuid=project_uuid, connection_ids=connection_ids)

        try:
            return self.app.agent_manager.execute(ASK_MODE_PROPERTIES_MAP.EXECUTE_AGENT.value, tool_executor, message, context=context, temperature=0.1 if 'gpt-5' not in LLM_MODEL else 1)

        except Exception as e:
            logger.exception(f"Error generating answer: {str(e)}")
            return f"Sorry, an error occurred while generating the answer: {str(e)}"

    def _gen_ans_agent(self, message, project_uuid, connection_ids, context):
        tool_executor = SmolAgentsToolExecutor()
        self.search_tools.registers(tool_executor, project_uuid=project_uuid, connection_ids=connection_ids)
        if 'gpt-5' not in LLM_MODEL:
            server_model = LiteLLMModel(client=self.app.llm_api, model_id=LLM_MODEL, context=context) 
        else:
            server_model = LiteLLMModel(client=self.app.llm_api, model_id=LLM_MODEL, context=context, temperatute=1)

        return self.app.agent_manager.execute(AGENT_MODE_PROPERTIES_MAP.EXECUTE_AGENT.value, tool_executor, message, server_model=server_model, context=context)
