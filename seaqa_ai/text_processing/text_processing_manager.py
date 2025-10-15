import logging
import json
from seaqa_ai.config import LLM_MODEL
from seaqa_ai.agent.tools import Tools
from seaqa_ai.utils.tools_executor import FunctionCallingToolExecutor, SmolAgentsToolExecutor
from seaqa_ai.prompts.ask import MAX_SOURCES_COUNT, system_prompts
from smolagents.models import LiteLLMModel


logger = logging.getLogger(__name__)


class TextProcessingManager:
    def __init__(self, app):
        self.app = app

    def _search(self, query, project_uuid, connection_ids):
        """Execute the retrieval based on the provided query."""
        assert isinstance(query, str), "Your search query must be a string"

        # Retrieve relevant documents
        docs = self.app.seaqa_indexer_api.search(query, project_uuid, connection_ids)

        # Format the retrieved documents for readability
        return "\nRetrieved documents:\n" + "".join(
            [
                f"\n\n===== Document {str(i + 1)} ===== Source: {doc.get('url')} =====\n" + doc.get('content')
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

        messages = [
            {
                'role': 'system',
                'content': system_prompts
            },
            {
                'role': 'user',
                'content': question
            }
        ]
        try:
            tool_calls = self.app.llm_api.completion(
                messages=messages,
                context=context,
                tools=tool_executor.get(),
                temperature=0.1 if 'gpt-5' not in LLM_MODEL else 1,
            ).choices[0].message.tool_calls

            tool_call = tool_calls[0]

            messages.append({
                'role': 'assistant',
                'tool_calls': tool_calls
            })

            messages.append({
                'role': 'tool',
                'tool_call_id': tool_call['id'],
                'content': json.dumps(tool_executor.execute(tool_call))
            })

            content = json.loads(self.app.llm_api.completion(
                messages=messages,
                context=context,
                temperature=0.1 if 'gpt-5' not in LLM_MODEL else 1,
            ).choices[0].message.content)

            return content['answer'], content['sources']
        
        except Exception as e:
            logger.error(f"Error generating answer: {str(e)}")
            return f"Sorry, an error occurred while generating the answer: {str(e)}"
    
    def _gen_ans_agent(self, message, project_uuid, connection_ids, context):
        tool_executor = SmolAgentsToolExecutor()
        tool_executor.register(self._search, Tools['keywords_search'], project_uuid=project_uuid, connection_ids=connection_ids)
        client = self.app.llm_api
        server_model = LiteLLMModel(client=client, model_id=LLM_MODEL, context=context)
        agent_name = 'tool_call_agent'
        answer, agent_memory, sources = self.app.agent_manager.execute(agent_name, message, tools=tool_executor.tools, server_model=server_model)
        top_sources = sources[:MAX_SOURCES_COUNT] if len(sources) > MAX_SOURCES_COUNT else sources

        return answer, agent_memory, top_sources
