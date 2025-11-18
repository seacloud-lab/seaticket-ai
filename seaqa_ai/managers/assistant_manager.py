import json
import logging

from seaqa_ai.agent.agent_manager import TMEPERATURE


logger = logging.getLogger(__name__)


class AIUtils:
    def __init__(self, app):
        self.app = app


    def gen_ticket_from_record(self, record_detail, context):
        """
        Generate a ticket with a title and description based on the input title and body content.
        """
        try:
            prompt = f"""Based on the provided ticket information, please generate a concise and informative ticket.

            {record_detail}

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

            response = self.app.llm_api.completion(
                messages=messages,
                context=context,
                temperature=TMEPERATURE,
            )
            resp_json = json.loads(response.choices[0].message.content)
            ai_title = resp_json.get('title', '')
            ai_description = resp_json.get('description', '')
            return ai_title, ai_description

        except Exception as e:
            logger.error(f"Error generating ticket: {str(e)}")
            return '', ''

    def generate_summary(self, connection_type, content, context):
        try:
            if connection_type == 'github_issue':
                messages = [
                {
                    "role": "system",
                    "content": (
                        "You are a GitHub issue analysis expert. Read the issue's title and body, then craft a "
                        "single-sentence English summary describing the core problem. Keep it concise (no more "
                        "than 100 characters) and return only that sentence without extra commentary or formatting."
                    )
                },
                {
                    "role": "user",
                    "content": f"""{content}"""
                }
            ]
            elif connection_type == 'site' or connection_type == 'seafile':
                messages = [
                    {
                        "role": "system",
                        "content": (
                            "You are a document summarization expert. Read the document, then craft a single-sentence English summary describing the core content."
                            "The summary should capture the main points and themes of the document clearly and effectively."
                            "Keep it concise (no more than 100 characters) and return only that sentence without extra commentary or formatting."
                        )
                    },
                    {"role": "user", "content": 'Summarize the following content' + content}
                ]

            elif connection_type == 'discourse_forum':
                messages = [
                {
                    "role": "system",
                    "content": (
                        "You are a Discourse forum post analysis expert. Read the post's title and body, then craft a "
                        "single-sentence English summary describing the core topic or request. Keep it concise (no more "
                        "than 100 characters) and return only that sentence without extra commentary or formatting."
                    )
                },
                {
                    "role": "user",
                    "content": f"""{content}"""
                }
            ] 

            response = self.app.llm_api.completion(
                messages=messages,
                context=context or {},
                temperature=TMEPERATURE,
                max_tokens=100
            )
            return response.choices[0].message.content.strip()
        except Exception as e:
            logger.error(f"Error generating summary: {e}")
            return ''
