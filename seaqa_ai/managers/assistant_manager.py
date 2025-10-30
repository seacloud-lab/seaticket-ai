import logging

from seaqa_ai.config import LLM_MODEL


logger = logging.getLogger(__name__)


class AssistantManager:
    def __init__(self, app):
        self.app = app

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
