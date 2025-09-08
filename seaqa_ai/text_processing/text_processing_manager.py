import logging
import json
from seaqa_ai.config import LLM_MODEL
from seaqa_ai.agent.tools import KeywordsSearch
from seaqa_ai.agent.models import LiteLLMModel


logger = logging.getLogger(__name__)


class TextProcessingManager:
    def __init__(self, app):
        self.app = app

    def generate_search_keywords(self, query, context):
        """
        Generate English and Chinese search keywords based on user query.
        """
        try:
            prompt = f"""Based on the user's question, please:
            1. Identify the language of the user's question
            2. Generate keywords that would help find relevant information.

            User Question: {query}

            Please provide:
            1. Keywords (3-5 key terms or phrases)

            Format your response as JSON:
            {{
                "keywords": "keyword1 keyword2 keyword3"
            }}

            Only return the JSON, no additional text."""
            messages = [
                {
                    "role": "system",
                    "content": "You are a professional search keyword generator. Generate relevant search keywords. Always respond with valid JSON format."
                },
                {"role": "user", "content": prompt}
            ]

            response = self.app.llm_api.run(
                messages=messages,
                context=context,
                temperature=0.1,
            )
            content = response.choices[0].message.content

            response_data = json.loads(content.strip())
            return response_data.get("keywords")

        except Exception as e:
            logger.error(f"Error generating keywords: {str(e)}")
            # Fallback to original query
            return query

    def generate_answer(self, question, sources, context):
        """
        Generate an answer based on the question and references.
        """
        try:
            # Extract top 5 sources
            top_sources = sources[:5] if len(sources) > 5 else sources

            # Build reference texts
            reference_texts = []
            for i, source in enumerate(top_sources, 1):
                content = source.get('content', '')
                title = source.get('title', 'Unknown Title')
                url = source.get('url', '')

                reference_text = f"Reference {i}:\nTitle: {title}\nContent: {content}"
                if url:
                    reference_text += f"\nSource: {url}"
                reference_texts.append(reference_text)

            references_content = "\n\n".join(reference_texts)

            # Build optimized prompt
            prompt = f"""You are a professional technical assistant. Please answer the user's question based on the provided reference materials.
            **Requirements:**
            1. Please answer in the language of the question first
            2. Keep your answer within 500 words
            3. Cite specific references using [Reference X] format when mentioning information from the sources
            4. Provide accurate, detailed, and helpful information
            5. If the reference materials are insufficient to fully answer the question, clearly indicate this

            **User Question:** {question}

            **Reference Materials:**
            {references_content}

            **Instructions:**
            - Use [Reference 1], [Reference 2], etc. to cite specific sources when mentioning information
            - Focus on the most relevant information from the references
            - Be concise but comprehensive within the word limit
            - Maintain professional and helpful tone"""

            # Build message format with enhanced system prompt
            messages = [
                {
                    "role": "system",
                    "content": f"You are a professional technical assistant specializing in providing accurate answers based on reference materials. Always cite your sources using [Reference X] format and keep responses within 500 words. Please answer in the language of the question first."
                },
                {
                    "role": "user",
                    "content": prompt
                }
            ]

            response = self.app.llm_api.run(
                messages=messages,
                context=context,
                temperature=0.1,
            )
            content = response.choices[0].message.content

            return content, top_sources

        except Exception as e:
            logger.error(f"Error generating answer: {str(e)}")
            return f"Sorry, an error occurred while generating the answer: {str(e)}"

    def generate_answer_by_tool_call_agent(self, message, project_uuid, connection_types, connection_ids, context):
        keywords_search = KeywordsSearch(self.app.seaqa_indexer_api, project_uuid, connection_types, connection_ids)
        client = self.app.llm_api
        server_model = LiteLLMModel(client, model_id=LLM_MODEL)
        server_model.context = context
        agent_name = 'tool_call_agent'

        response = self.app.agent_manager.execute(agent_name,  message, retriever_tool=keywords_search, server_model=server_model)

        return response, []
