import logging
from seaqa_ai.config import LLM_MODEL
from seaqa_ai.agent.tools import Tools
from seaqa_ai.utils.tools_executor import FunctionCallingToolExecutor, SmolAgentsToolExecutor
from seaqa_ai.prompts.ask import MAX_SOURCES_COUNT
from smolagents.models import LiteLLMModel

from seaqa_ai.db import init_db_session_class
from seaqa_ai.utils.seaqa_db import SeaqaDB
from seaqa_ai.utils.constants import ConnectionType

logger = logging.getLogger(__name__)


class ChatManager:
    def __init__(self, app):
        self.app = app
        self.db_session_class = init_db_session_class()
        self.seaqa_db = SeaqaDB(self.db_session_class)

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

    def _gen_ans_ask(self, message, project_uuid, connection_ids, context):
        """
        Generate an answer based on the question and references.
        """
        tool_executor = FunctionCallingToolExecutor()
        tool_executor.register(self._search, Tools['keywords_search'], project_uuid=project_uuid, connection_ids=connection_ids)

        try:
            return self.app.agent_manager.execute('function_calling', message, tool_executor=tool_executor, context=context, temperature=0.1 if 'gpt-5' not in LLM_MODEL else 1)

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

        return self.app.agent_manager.execute('reasoning_agent', message, tool_executor=tool_executor, server_model=server_model)

    def generate_ticket(self, title, body_content, context):
        """
        Generate a ticket with a title and description based on the input title and body content.
        """
        try:
            prompt = f"""Based on the provided ticket information, please generate a concise and informative ticket.

            **Ticket Information:**
            Title: {title}
            Body: {body_content}

            **Instructions:**
            1. Generate a new, clear, and concise title for the ticket.
            2. Create a detailed description that summarizes the key points from the body content.
            3. The output language must be consistent with the input language.
            4. Return the result in JSON format with "title" and "description" keys.

            **Example Output:**
            {{
                "title": "New Ticket Title",
                "description": "A detailed summary of the ticket body."
            }}
            """

            messages = [
                {
                    "role": "system",
                    "content": "You are an expert ticket-processing assistant. Your task is to create well-structured tickets from provided information, ensuring the output is in JSON format and matches the input language."
                },
                {
                    "role": "user",
                    "content": prompt
                }
            ]

            response = self.app.openai_api.chat_completions(
                messages=messages,
                context=context,
                temperature=0.1,
            )

            ticket_data = json.loads(response.strip())
            ai_title = ticket_data.get('title', title)
            ai_description = ticket_data.get('description', '')
            return ai_title, ai_description

        except Exception as e:
            logger.error(f"Error generating ticket: {str(e)}")
            return title, ''

    def create_ticket_info(self, connection_id, record_id, context):
        connection_type = self.seaqa_db.get_connection_type_by_id(connection_id)
        if connection_type == ConnectionType.DISCOURSE_FORUM.value:
            title = self.seaqa_db.get_topic_title_by_id(connection_id, record_id)
            replies = self.seaqa_db.get_replies_for_topic(connection_id, record_id)
            body_content = ''
            for reply in replies:
                if not reply.content:
                    continue

                content_to_add = reply.content
                if body_content:
                    content_to_add = '\n\n' + content_to_add
                if len(body_content) + len(content_to_add) > 600:
                    break
                body_content += content_to_add
        
        title, description = self.generate_ticket(title, body_content, context)
        return title, description
