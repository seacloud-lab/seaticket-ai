import json
import logging

from seaqa_ai.config import LLM_MODEL


logger = logging.getLogger(__name__)


class AssistantManager:
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
                temperature=0.1,
            )
            resp_json = json.loads(response.choices[0].message.content)
            ai_title = resp_json.get('title', '')
            ai_description = resp_json.get('description', '')
            return ai_title, ai_description

        except Exception as e:
            logger.error(f"Error generating ticket: {str(e)}")
            return '', ''


    def generate_title_from_content(self, title, body, context):
        try:
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
                    "content": f"""Current title: {title}\nBody: {body}"""
                }
            ]

            response = self.app.llm_api.completion(
                messages=messages,
                context=context or {},
                temperature=0.1 if 'GPT-5' not in LLM_MODEL else 1,
                max_tokens=100
            )

            generated_title = response.choices[0].message.content.strip()
            return generated_title

        except Exception as e:
            logger.error(f"Error generating title: {e}")
            return ''
