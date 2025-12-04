import logging
import json_repair

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
            resp_json = json_repair.loads(response.choices[0].message.content)
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
            raise

    def rank_issues_by_relevance(self, query_record, candidate_records, context):
        try:
            if not candidate_records:
                return []

            candidates_text = ""
            candidate_keys = []
            for candidate in candidate_records:
                composite_key = f"{candidate['_id']}:{candidate.get('connection_id', 'unknown')}"
                candidate_keys.append(composite_key)
                candidates_text += f""" \n Record Key: {composite_key} \n Title: {candidate.get('title', 'N/A')} \n Summary: {candidate.get('ai_summary', 'N/A')} \n"""

            total_candidates = len(candidate_records)
            prompt = (f"""
                You are an expert in analyzing the relevance between records. Given a query record and a list of candidate records, your task is to rank the candidates by their relevance to the query record.

                Query Record:
                Title: {query_record.get('title', 'N/A')}
                Summary: {query_record.get('ai_summary', 'N/A')}

                Total Number of Candidate Records: {total_candidates}

                Candidate Records:
                {candidates_text}

                **CRITICAL Instructions:**
                1. You MUST rank ALL {total_candidates} candidate records provided above.
                2. Analyze the semantic similarity and topical relevance between the query record and each candidate record.
                3. Consider both the title and summary when determining relevance.
                4. Return a JSON array with EXACTLY {total_candidates} record keys (in format "record_id:connection_id") ordered from most relevant to least relevant.
                5. Do NOT skip any records - every Record Key shown above must appear in your output.
                6. Do NOT add any explanation, commentary, or additional text - return ONLY the JSON array.
                7. Double-check that your output contains all {total_candidates} keys before returning.

                **Example Output Format (if there were 5 records):**
                ["record_id_3:conn_1", "record_id_1:conn_2", "record_id_5:conn_1", "record_id_2:conn_3", "record_id_4:conn_1"]
            """)

            messages = [
                {
                    "role": "system",
                    "content": "You are an expert relevance ranking system. Your task is to analyze records and rank them by relevance. You MUST return ALL candidate records in your ranking without omitting any. Always return a valid JSON array of composite keys (record_id:connection_id) without any additional text or explanation."
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

            result = response.choices[0].message.content.strip()
            reranked_keys = json_repair.loads(result)

            candidate_key_set = {f"{c['_id']}:{c.get('connection_id', 'unknown')}" for c in candidate_records}

            validated_keys = []
            seen_keys = set()
            for key in reranked_keys:
                if key in candidate_key_set and key not in seen_keys:
                    validated_keys.append(key)
                    seen_keys.add(key)

            missing_keys = [f"{c['_id']}:{c['connection_id']}"
                           for c in candidate_records
                           if f"{c['_id']}:{c['connection_id']}" not in seen_keys]
            validated_keys.extend(missing_keys)

            return validated_keys

        except Exception as e:
            logger.error(f"Error ranking related records: {e}")
            raise
