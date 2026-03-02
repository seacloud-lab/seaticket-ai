import json
import logging
import json_repair
import uuid
from copy import copy
from seaqa_ai.config import AI_UTILS_TIER, SQLALCHEMY_DATABASE_URI
from seaqa_ai.db import init_db_session_class
from seaqa_ai.managers.ai_utils.prompts import AI_UTILS_PROMPTS_MAP, UserContentPrompts, GENERATE_SUMMAEY_PROMPTS_MAP
from seaqa_ai.managers.ai_utils.schemas import SCHEMAS_MAP
from seaqa_ai.utils import retrieve_content_of_search_results, set_ai_summary, search_records_to_map, convert_to_json_code_block, gen_completion_format
from seaqa_ai.utils.constants import ExtraSourceType, MODEL_REASONING_TIER, LANGUAGE
from seaqa_ai.utils.llm_api import get_llm_client_by_model_tier
from seaqa_ai.utils.seaqa_db import SeaqaDB

logger = logging.getLogger(__name__)


class AIUtils:
    def __init__(self, app):
        self.app = app
        self.db_session_class = init_db_session_class(SQLALCHEMY_DATABASE_URI)
        self.seaqa_db = SeaqaDB(self.db_session_class)

    def gen_ticket_from_record(self, record_detail, context):
        """
        Generate a ticket with a title and description based on the input title and body content.
        """
        try:
            messages = [
                {
                    "role": "system",
                    "content": AI_UTILS_PROMPTS_MAP['generate_ticket']
                },
                {
                    "role": "user",
                    "content": UserContentPrompts.generate_ticket(record_detail)
                }
            ]

            llm_client = get_llm_client_by_model_tier(self.app.data_logger, AI_UTILS_TIER.get('generate_ticket', MODEL_REASONING_TIER.LOW.value))

            response = llm_client.completion(
                messages=messages,
                context=context,
                response_format=gen_completion_format(SCHEMAS_MAP['generate_ticket'])
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

    def gen_kb_record_from_ticket(self, ticket_title, ticket_content, ticket_comments, context):
        """
        Generate a knowledge base record (kb_title, kb_content) from a ticket.
        """
        try:
            messages = [
                {
                    'role': 'system',
                    'content': AI_UTILS_PROMPTS_MAP['generate_kb_record_from_ticket']
                },
                {
                    'role': 'user',
                    'content': UserContentPrompts.generate_kb_record_from_ticket(ticket_title, ticket_content, ticket_comments)
                }
            ]

            llm_client = get_llm_client_by_model_tier(
                self.app.data_logger,
                AI_UTILS_TIER.get('generate_kb_record_from_ticket', MODEL_REASONING_TIER.LOW.value)
            )

            response = llm_client.completion(
                messages=messages,
                context=context,
                response_format=gen_completion_format(SCHEMAS_MAP['generate_kb_record_from_ticket'])
            ).choices[0].message.content

            try:
                result = json_repair.loads(response, strict=True)
            except Exception as e:
                logger.exception(f'Failure to parse kb record result: {e}')
                return '', ''

            kb_title = result.get('kb_title', '')
            kb_content = result.get('kb_content', '')
            return kb_title, kb_content

        except Exception as e:
            logger.error(f"Error generating knowledge base record: {str(e)}")
            return '', ''

    def generate_summary(self, connection_type, project_uuid,  content, context):
        try:
            project_settings = self.seaqa_db.get_project_settings_by_project_uuid(project_uuid)
            settings = json.loads(project_settings) if project_settings else {}
            lang = settings.get('lang')
        except Exception as e:
            logger.warning(
                "Failed to load/parse project settings, fallback to default language. error: %s", e)
            lang = 'en'

        try:
            system_prompts = GENERATE_SUMMAEY_PROMPTS_MAP.get(connection_type, GENERATE_SUMMAEY_PROMPTS_MAP['documents'])
            target_lang = LANGUAGE.get(lang, 'English')
            lang_instruction = (
                f"Output language requirement: You must write the summary in {target_lang}. "
            )
            messages = [
                {
                    "role": "system",
                    "content": f"{system_prompts}\n\n{lang_instruction}"
                },
                {
                    "role": "user",
                    "content": f"""{content}"""
                }
            ]
            
            llm_client = get_llm_client_by_model_tier(self.app.data_logger, AI_UTILS_TIER.get('generate_summary', MODEL_REASONING_TIER.LOW.value))

            response = llm_client.completion(
                messages=messages,
                context=context or {},
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
                try:
                    record_id = int(record_id)
                except:
                    pass
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

            messages = [
                {
                    "role": "system",
                    "content": AI_UTILS_PROMPTS_MAP['rank_relevant_records']
                },
                {
                    "role": "user",
                    "content": UserContentPrompts.rank_relevant_records(query_record, candidates_text)
                }
            ]

            llm_client = get_llm_client_by_model_tier(self.app.data_logger, AI_UTILS_TIER.get('rerank_relevance', MODEL_REASONING_TIER.MEDIUM.value))

            response = llm_client.completion(
                messages=messages,
                context=context,
                response_format=gen_completion_format(SCHEMAS_MAP['rank_relevant_records'], 'rank_relevant_records')
            )

            token_usage = {
                'input_tokens': response.usage.prompt_tokens,
                'output_tokens': response.usage.completion_tokens,
                'total_tokens': response.usage.total_tokens
            }

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
                if {'connection_id': connection_id, 'record_id': record_id} in candidate_keys and \
                    f'{record_id}:{connection_id}' not in validated_keys:
                    validated_keys.append(f'{record_id}:{connection_id}')
            return validated_keys, token_usage

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
        ranked_record_id_connection_pairs, rerank_token_usage = self.rank_issues_by_relevance(query, new_records, context)
        ranked_record_id_connection_pairs = ranked_record_id_connection_pairs[:limit]
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

        return search_result_after_ranked, rerank_token_usage
    
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

        search_results, rerank_token_usage = self._rank_search_results(query, search_results, context, limit, project_uuid, connection_id_type_map)

        # reformat to list
        sources_results = []
        for record_id_record_map in search_results.values():
            for record in record_id_record_map.values():
                sources_results.append(record)
        
        return sources_results, rerank_token_usage
