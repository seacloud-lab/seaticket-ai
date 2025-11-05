import logging
import json
from seaqa_ai.config import LLM_MODEL, ENABLE_TOOLS_SHARED_VARS
from seaqa_ai.agent.tools import Tools
from seaqa_ai.agent.utils import get_search_results_from_db
from seaqa_ai.agent.tool_shared_vars import ToolSharedVars
from seaqa_ai.utils.tools_executor import FunctionCallingToolExecutor, SmolAgentsToolExecutor
from seaqa_ai.utils.constants import ASK_MODE_PROPERTIES_MAP, AGENT_MODE_PROPERTIES_MAP
from seaqa_ai.prompts.ask import MAX_SOURCES_COUNT
from smolagents.models import LiteLLMModel

logger = logging.getLogger(__name__)


class ChatManager:
    def __init__(self, app):
        self.app = app

    def _get_preview(self, doc_content):
        # reserved interface
        return doc_content[:100]

    def _search(self, query, project_uuid, connection_ids, context):
        """Execute the retrieval based on the provided query."""
        assert isinstance(query, str), "Your search query must be a string"

        # Retrieve relevant documents
        sources_results = ToolSharedVars.get('sources_results', []) if ENABLE_TOOLS_SHARED_VARS else get_search_results_from_db(self.app.agent_manager.db_session_class, context['chat_uuid'])
        
        to_observation = ''
        to_tools_shared_vars = {
            'sources_results': sources_results
        }
        to_db = []

        for doc in self.app.seaqa_indexer_api.search(query, project_uuid, connection_ids)[:MAX_SOURCES_COUNT]:
            if len(sources_results) >= MAX_SOURCES_COUNT:
                break

            doc.update({
                'title': doc['title'] if doc.get('title') else doc.get('filename')
            })
            to_observation += f"\nRetrieved documents:\n\n\n===== Document {len(to_tools_shared_vars['sources_results'])} ===== Title: {doc.get('title', '')} =====\n{doc.get('content', '')}"
            doc['content_preview'] = self._get_preview(doc['content'])
            del doc['content'] # discard content, only save metadata
            to_tools_shared_vars['sources_results'].append(doc)
            to_db.append(doc)

        # Format the retrieved documents for readability
        return {
            'to_observation': to_observation,
            'to_tools_shared_vars': to_tools_shared_vars,
            'to_db': to_db
        }

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
        tool_executor.register(self._search, Tools['keywords_search'], project_uuid=project_uuid, connection_ids=connection_ids, context=context)

        try:
            return self.app.agent_manager.execute(ASK_MODE_PROPERTIES_MAP.EXECUTE_AGENT.value, message, tool_executor=tool_executor, context=context, temperature=0.1 if 'gpt-5' not in LLM_MODEL else 1)

        except Exception as e:
            logger.exception(f"Error generating answer: {str(e)}")
            return f"Sorry, an error occurred while generating the answer: {str(e)}"

    def _gen_ans_agent(self, message, project_uuid, connection_ids, context):
        tool_executor = SmolAgentsToolExecutor()
        tool_executor.register(self._search, Tools['keywords_search'], project_uuid=project_uuid, connection_ids=connection_ids, context=context)
        if 'gpt-5' not in LLM_MODEL:
            server_model = LiteLLMModel(client=self.app.llm_api, model_id=LLM_MODEL, context=context) 
        else:
            server_model = LiteLLMModel(client=self.app.llm_api, model_id=LLM_MODEL, context=context, temperatute=1)

        return self.app.agent_manager.execute(AGENT_MODE_PROPERTIES_MAP.EXECUTE_AGENT.value, message, tool_executor=tool_executor, server_model=server_model, context=context)
