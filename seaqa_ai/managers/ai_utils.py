import logging
import json_repair
import uuid
from seaqa_ai.utils import retrieve_content_of_search_results, set_ai_summary, search_records_to_map
from seaqa_ai.utils.constants import ConnectionType
from seaqa_ai.utils.llm_api import LLMAPI
from seaqa_ai.config import get_light_weight_model_config


logger = logging.getLogger(__name__)


class AIUtils:
    def __init__(self, app):
        self.app = app
        light_weight_model_config = get_light_weight_model_config()
        self.llm_api = LLMAPI(app.data_logger,
            light_weight_model_config['type'],
            light_weight_model_config['url'],
            light_weight_model_config['key'],
            light_weight_model_config['model']
        )
        self.temperature = 0.1 if 'gpt-5' not in light_weight_model_config['model'] else 1


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

            
            response = self.llm_api.completion(
                messages=messages,
                context=context,
                temperature=self.temperature,
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
            elif connection_type == 'email':
                messages = [
                {
                    "role": "system",
                    "content": (
                        "You are a email analysis expert. Read the email's title and body, then craft a "
                        "single-sentence English summary describing the core topic or request. Keep it concise (no more "
                        "than 100 characters) and return only that sentence without extra commentary or formatting."
                    )
                },
                {
                    "role": "user",
                    "content": f"""{content}"""
                }
            ]

            response = self.llm_api.completion(
                messages=messages,
                context=context or {},
                temperature=self.temperature,
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
            if isinstance(query_record, str):
                query_record = {'ai_summary': query_record}

            candidates_text = ""
            candidate_keys = []
            for candidate in candidate_records:
                if '_id' in candidate:
                    key = candidate['_id']
                elif 'pk' in candidate:
                    key = candidate['pk']
                else:
                    continue
                key = f"{key}:{candidate.get('connection_id', 'unknown')}"
                candidate_keys.append(key)
                candidates_text += f""" \n Record Key: {key} \n Title: {candidate.get('title', 'N/A')} \n Summary: {candidate.get('ai_summary', 'N/A')} \n"""

            prompt = (f"""
                You are an expert in analyzing the relevance between records. Given a query record and a list of candidate records, your task is to identify and rank ONLY the relevant candidates.

                Query Record:
                Title: {query_record.get('title', 'N/A')}
                Summary: {query_record.get('ai_summary', 'N/A')}

                Candidate Records:
                {candidates_text}

                **CRITICAL Instructions:**
                1. Analyze the semantic similarity and topical relevance between the query record and each candidate record.
                2. Consider both the title and summary when determining relevance.
                3. ONLY include records that are truly relevant to the query record. A record is relevant if it:
                   - Addresses similar topics, issues, or problems
                   - Has related technical content or features
                   - Shares common themes or concepts
                   - Could provide useful context or related information
                4. Exclude records that are unrelated, off-topic, or have no meaningful connection to the query.
                5. Return a JSON array containing ONLY the relevant record keys (in format "record_id:connection_id") ordered from most relevant to least relevant.
                6. If no records are relevant, return an empty array: []
                7. Do NOT add any explanation, commentary, or additional text - return ONLY the JSON array.

                **Example Output Format:**
                ["record_id_3:conn_1", "record_id_1:conn_2", "record_id_5:conn_1"]

                Or if no records are relevant:
                []
            """)

            messages = [
                {
                    "role": "system",
                    "content": "You are an expert relevance ranking system. Your task is to analyze records and identify only those that are truly relevant to the query. Return a valid JSON array of composite keys (record_id:connection_id) for relevant records only, ordered by relevance. Return an empty array if no records are relevant."
                },
                {
                    "role": "user",
                    "content": prompt
                }
            ]

            response = self.llm_api.completion(
                messages=messages,
                context=context,
                temperature=self.temperature,
            )

            result = response.choices[0].message.content.strip()
            reranked_keys = json_repair.loads(result)

            candidate_key_set = set(candidate_keys)

            validated_keys = []
            seen_keys = set()
            for key in reranked_keys:
                # sometime ai response a dict not a str
                if isinstance(key, dict):
                    record_id = key.get('record_id')
                    connection_id = key.get('connection_id')
                    if record_id and connection_id:
                        key = f'{record_id}:{connection_id}'
                    else:
                        continue
                if key in candidate_key_set and key not in seen_keys:
                    validated_keys.append(key)
                    seen_keys.add(key)
            return validated_keys

        except Exception as e:
            logger.error(f"Error ranking related records: {e}")
            raise

    def _rank_search_results(self, query, search_result, context, limit, project_uuid, connection_id_type_map):
        """
        search_results: a map of {
            'connection_id': {
                'record_id': record # 'record_id', i.e., _pk in seadb, _id in seaqa-indexer
            }
        }
        """
        # rank relevance
        new_records = [] # after reset ai_summary for site connection
        for record_id_record_map in search_result.values():
            for record in record_id_record_map.values():
                new_records.append(record)
        ranked_record_id_connection_pairs = self.rank_issues_by_relevance(query, new_records, context)[:limit]
        search_result_after_ranked = {}
        rank_pairs = []
        for pair in ranked_record_id_connection_pairs:
            if len((after_unpack := pair.split(':'))) != 2:
                continue
            record_id, connection_id = after_unpack
            try:
                connection_id = int(connection_id)
            except:
                connection_id = connection_id
            try:
                record_id = int(record_id)
            except:
                logger.warning(f'Invalid record_id: {record_id}')
            if connection_id in search_result and record_id in search_result[connection_id]:
                rank_pairs.append({
                    'connection_id': connection_id,
                    'record_id': record_id
                })
                if connection_id not in search_result_after_ranked:
                    search_result_after_ranked[connection_id] = {}
                search_result_after_ranked[connection_id][record_id] = search_result[connection_id][record_id]
        
        # get source results for records after ranked
        search_result_after_ranked = retrieve_content_of_search_results(search_result_after_ranked, self.app.seadb_api, project_uuid, connection_id_type_map)

        return search_result_after_ranked
    
    def search(self, query, connections, context, limit=100):
        if limit <= 0:
            return [], ''

        connection_ids = ''
        connection_id_type_map = {}
        for connection in connections:
            connection_ids += str(connection['id']) + ','
            connection_id_type_map[connection['id']] = connection['type']
        if connection_ids:
            connection_ids = connection_ids[:-1]
        connection_id_type_map['unknown'] = ConnectionType.KNOWLEDGE_BASE.value

        # keywords search data
        project_uuid = context['project_uuid']
        records = self.app.seaqa_indexer_api.keywords_search(query, project_uuid, connection_ids, context['username'])
        search_results = search_records_to_map(records, {})

        # semantic search data
        records = self.app.seaqa_indexer_api.vector_search(query, project_uuid, connection_ids, context['username'])
        search_results = search_records_to_map(records, search_results)

        # reset value of vector_retrieval_type, e.g., site connection
        project_uuid = str(uuid.UUID(project_uuid))
        search_results = set_ai_summary(search_results, self.app.seadb_api, project_uuid, connection_id_type_map)

        search_results = self._rank_search_results(query, search_results, context, limit, project_uuid, connection_id_type_map)

        # reformat to list
        sources_results = []
        for record_id_record_map in search_results.values():
            for record in record_id_record_map.values():
                sources_results.append(record)
        
        return sources_results
