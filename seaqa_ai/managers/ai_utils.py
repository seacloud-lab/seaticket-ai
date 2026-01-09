import logging
import json_repair
import uuid
from copy import copy
from seaqa_ai.utils import retrieve_content_of_search_results, set_ai_summary, search_records_to_map, convert_to_json_code_block, gen_completion_format
from seaqa_ai.utils.constants import ExtraSourceType
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
            """

            messages = [
                {
                    "role": "system",
                    "content": "You are an expert ticket-processing assistant. Your task is to create well-structured tickets from provided information."
                },
                {
                    "role": "user",
                    "content": prompt
                }
            ]

            response_schema = {
                'type': 'object',
                'properties': {
                    'title': {
                        'type': 'string',
                        'description': 'New Ticket Title'
                    },
                    'description': {
                        'type': 'string',
                        'description': 'A detailed summary of the ticket body'
                    }
                },
                'additionalProperties': False,
                'required': ['title', 'description']
            }

            response = self.llm_api.completion(
                messages=messages,
                context=context,
                temperature=self.temperature,
                response_format=gen_completion_format(response_schema)
            ).choices[0].message.content

            try:
                result = json_repair.loads(response, strict=True)
            except Exception as e:
                logger.exception(f'Failure to parse ticket result: {e}')
                return '', ''

            ai_title = result.get('title', '')
            ai_description = result.get('description', '')
            return ai_title, ai_description

        except Exception as e:
            logger.error(f"Error generating ticket: {str(e)}")
            return '', ''

    def generate_summary(self, type, content, context):
        try:
            if type == 'github_issue':
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
            elif type == 'site' or type == 'seafile' or type == 'knowledge_base':
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

            elif type == 'discourse_forum':
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
            elif type == 'email':
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
            elif type == 'ticket':
                messages = [
                    {
                        "role": "system",
                        "content": (
                            "You are a ticket analysis expert. A ticket: is a recorded unit of work that is created to track a request, "
                            "an issue, a task, or an incident: from its initiation to completion. Read the ticket's title and body, then craft a "
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
            if isinstance(query_record, dict):
                query_record = f"""Title: {query_record.get('title', 'N/A')}
Summary: {query_record.get('ai_summary', 'N/A')}"""

            candidates_text = []
            candidate_keys = []
            for candidate in candidate_records:
                if '_id' in candidate:
                    record_id = candidate['_id']
                elif 'pk' in candidate:
                    record_id = candidate['pk']
                else:
                    continue
                if candidate.get('type') in ExtraSourceType:
                    connection_id = candidate.get('type')
                else:
                    connection_id = candidate.get('connection_id', 'unknown')
                try:
                    connection_id = int(connection_id)
                except:
                    pass
                new_candidate = {
                    'connection_id': connection_id,
                    'record_id': record_id
                }
                candidate_keys.append(copy(new_candidate))
                if title := candidate.get('title'):
                    new_candidate['title'] = title
                if summary := candidate.get('ai_summary'):
                    new_candidate['summary'] = summary
                if snippets := candidate.get('snippets'):
                    new_candidate['snippets'] = snippets
                candidates_text.append(new_candidate)
            
            candidates_text = convert_to_json_code_block(candidates_text)

            prompt = (f"""You are an expert in analyzing the relevance between records. Given a query record and a list of candidate records, your task is to identify and rank ONLY the relevant candidates.

Query Record:
{query_record}

Candidate Records:
{candidates_text}

**CRITICAL Instructions:**
1. Analyze the semantic similarity and topical relevance between the query record and each candidate record.
2. Consider both the title and other fields when determining relevance. Meanwhile, the title field of the candidate record has a higher relevance weight.
3. ONLY include records that are truly relevant to the query record. A record is relevant if it:
    - Addresses similar title, topics, issues, or problems
    - Has related technical content or features
    - Shares common themes or concepts
    - Could provide useful context or related information
4. Exclude records that are unrelated, off-topic, or have no meaningful connection to the query.
""")

            messages = [
                {
                    "role": "system",
                    "content": "You are an expert on relevance ranking. Your task is to analyze records and identify only those that are truly relevant to the query"
                },
                {
                    "role": "user",
                    "content": prompt
                }
            ]

            response_schema = {
                'type': 'object',
                'properties': {
                    'results': {
                        'type': 'array',
                        'description': 'The relevant record keys ordered from most relevant to least relevant. If no records are relevant, set it to an empty array: []',
                        'items': {
                            'type': 'object',
                            'description': 'The key of a record',
                            'properties': {
                                'connection_id': {
                                    'type': 'string',
                                    'description': 'the connection_id field of the candidate record'
                                },
                                'record_id': {
                                    'type': 'string',
                                    'description': 'the record_id field of the candidate record'
                                },
                            },
                            'required': ['connection_id', 'record_id'],
                            'additionalProperties': False
                        }
                    }
                },
                'additionalProperties': False,
                'required': ['results']
            }

            response = self.llm_api.completion(
                messages=messages,
                context=context,
                temperature=self.temperature,
                response_format=gen_completion_format(response_schema)
            )

            response = response.choices[0].message.content

            try:
                results = json_repair.loads(response, strict=True).get('results', [])
            except Exception as e:
                logger.exception(f'Failure to rerank relevance: {e}')
                return []

            validated_keys = []
            for result in results:
                try:
                    connection_id = int(result.get('connection_id'))
                except:
                    connection_id = result.get('connection_id', 'unknown')
                try:
                    record_id = int(result.get('record_id'))
                except:
                    record_id = result.get('record_id')
                # sometime ai response a dict not a str
                if {'connection_id': connection_id, 'record_id': record_id} in candidate_keys:
                    validated_keys.append(f'{record_id}:{connection_id}')
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
    
    def search(self, query, connections, extra_sources, context, limit=100):
        if limit <= 0:
            return [], ''

        connection_ids = ''
        connection_id_type_map = {}
        for connection in connections:
            connection_ids += str(connection['id']) + ','
            connection_id_type_map[connection['id']] = connection['type']
        if connection_ids:
            connection_ids = connection_ids[:-1]
        connection_id_type_map[ExtraSourceType.KNOWLEDGE_BASE.value] = ExtraSourceType.KNOWLEDGE_BASE.value
        connection_id_type_map[ExtraSourceType.TICKET.value] = ExtraSourceType.TICKET.value

        # keywords search data
        project_uuid = context['project_uuid']
        records = self.app.seaqa_indexer_api.keywords_search(query, project_uuid, connection_ids, extra_sources, context['username'])
        search_results = search_records_to_map(records, {})

        # semantic search data
        records = self.app.seaqa_indexer_api.vector_search(query, project_uuid, connection_ids, extra_sources, context['username'])
        search_results = search_records_to_map(records, search_results, is_vector_search=True)

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
