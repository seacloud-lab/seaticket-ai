import logging
from seaqa_ai.config import LLM_MODEL
from seaqa_ai.agent.tools import Tools
from seaqa_ai.utils.tools_executor import FunctionCallingToolExecutor, SmolAgentsToolExecutor
from seaqa_ai.prompts.ask import MAX_SOURCES_COUNT
from smolagents.models import LiteLLMModel


logger = logging.getLogger(__name__)


class ChatManager:
    def __init__(self, app):
        self.app = app

    def _search(self, query, project_uuid, connection_ids):
        """Execute the retrieval based on the provided query."""
        assert isinstance(query, str), "Your search query must be a string"

        # Retrieve relevant documents
        docs = self.app.seaqa_indexer_api.search(query, project_uuid, connection_ids)[:MAX_SOURCES_COUNT]

        # Format the retrieved documents for readability
        return "\nRetrieved documents:\n" + "".join(
            [
                f"\n\n===== Document {str(i + 1)} ===== Source: {doc.get('url')} =====\n" + (doc.get('content') or '')
                for i, doc in enumerate(docs)
            ]
        )

    def generate_answer(self, resolve_type, message, project_uuid, connection_ids, context):
        if resolve_type == 'agent':
            return self._gen_ans_agent(message, project_uuid, connection_ids, context)
        elif resolve_type == 'ask':
            return self._gen_ans_ask(message, project_uuid, connection_ids, context)
        else:
            return f"Invalid resolve type"

    def _gen_ans_ask(self, question, project_uuid, connection_ids, context):
        """
        Generate an answer based on the question and references.
        """
        tool_executor = FunctionCallingToolExecutor()
        tool_executor.register(self._search, Tools['keywords_search'], project_uuid=project_uuid, connection_ids=connection_ids)

        try:
            return self.app.agent_manager.execute('function_calling', question, tool_executor=tool_executor, context=context, temperature=0.1 if 'gpt-5' not in LLM_MODEL else 1)

        except Exception as e:
            logger.exception(f"Error generating answer: {str(e)}")
            return f"Sorry, an error occurred while generating the answer: {str(e)}"

    def _gen_ans_agent(self, message, project_uuid, connection_ids, context):
        tool_executor = SmolAgentsToolExecutor()
        tool_executor.register(self._search, Tools['keywords_search'], project_uuid=project_uuid, connection_ids=connection_ids)
        if 'gpt-5' not in LLM_MODEL:
            server_model = LiteLLMModel(client=self.app.llm_api, model_id=LLM_MODEL, context=context) 
        else:
            server_model = LiteLLMModel(client=self.app.llm_api, model_id=LLM_MODEL, context=context, temperatute=1)

        return self.app.agent_manager.execute('tool_call_agent', message, tool_executor=tool_executor, server_model=server_model)
