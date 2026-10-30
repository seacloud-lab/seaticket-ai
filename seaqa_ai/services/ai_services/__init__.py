import json
import logging
import json_repair
import time
from copy import copy
from seaqa_ai.config import AI_UTILS_TIER, SQLALCHEMY_DATABASE_URI
from seaqa_ai.db import init_db_session_class
from seaqa_ai.services.ai_services.prompts import AI_UTILS_PROMPTS_MAP, UserContentPrompts, GENERATE_SUMMARY_PROMPTS_MAP
from seaqa_ai.services.ai_services.schemas import SCHEMAS_MAP
from seaqa_ai.utils import convert_to_json_code_block, uuid_str_to_32_chars
from seaqa_ai.utils import gen_completion_format, gen_record_hash
from seaqa_ai.utils.constants import ExtraSourceType, MODEL_REASONING_TIER, LANGUAGE
from seaqa_ai.utils.llm_api import get_completion_client_by_model_tier
from seaqa_ai.utils.seaqa_db import SeaqaDB
from seaqa_ai.services.ai_services.utils.search import search_records_to_map, get_title_and_ai_summary_of_search_results
from seaqa_ai.services.ai_services.utils.search.content_retriever import ContentRetriever
from seaqa_ai.services.chat_service.utils.callbacker import ChatCallBacker
from seaqa_ai.seadb_models.models import SchemaTables
from seaqa_ai.skills.registry import SkillRegistry

logger = logging.getLogger(__name__)

class AIServices:
    def __init__(self, app):
        self.app = app
        self.db_session_class = init_db_session_class(SQLALCHEMY_DATABASE_URI)
        self.seaqa_db = SeaqaDB(self.db_session_class)

    def _get_project_target_language(self, project_uuid):
        lang = 'en'
        if not project_uuid:
            return LANGUAGE.get(lang, 'English')

        try:
            project_settings = self.seaqa_db.get_project_settings_by_project_uuid(project_uuid)
            settings = json.loads(project_settings) if project_settings else {}
            lang = settings.get('lang') or lang
        except Exception as e:
            logger.warning(
                "Failed to load/parse project settings, fallback to default language. error: %s", e)

        return LANGUAGE.get(lang, 'English')

    def validate_chat_input(self, query, attachments, context):
        context = context or {}
        is_external_portal = context.get('is_external_portal', False)
        available_tools = SkillRegistry.available_tool_names({
            'is_external_portal': is_external_portal,
        })
        messages = [
            {
                'role': 'system',
                'content': AI_UTILS_PROMPTS_MAP['validate_chat_input'],
            },
            {
                'role': 'user',
                'content': UserContentPrompts.validate_chat_input(
                    query,
                    attachments=attachments,
                    project_prompt=context.get('project_prompt', ''),
                    portal_chat_prompt=context.get('portal_chat_prompt', ''),
                    is_external_portal=is_external_portal,
                    available_tools=available_tools,
                ),
            },
        ]
        images = [
            {
                'type': 'image_url',
                'image_url': {
                    'url': f"data:{attachment['mime_type']};base64,{attachment['data']}",
                },
            }
            for attachment in attachments
            if isinstance(attachment, dict)
            and attachment.get('type') == 'image'
            and attachment.get('mime_type')
            and attachment.get('data')
        ]
        if images:
            messages[1]['content'] = [
                {'type': 'text', 'text': messages[1]['content']},
                *images,
            ]
        llm_client = get_completion_client_by_model_tier(
            self.app.data_logger,
            AI_UTILS_TIER.get('validate_chat_input', MODEL_REASONING_TIER.LOW.value),
        )
        response = llm_client.completion(
            messages=messages,
            context=context,
            response_format=gen_completion_format(
                SCHEMAS_MAP['validate_chat_input'], 'validate_chat_input'
            ),
        ).choices[0].message.content

        result = json_repair.loads(response, strict=True)
        valid = result.get('valid')
        reason = result.get('reason')
        if not isinstance(valid, bool) or not isinstance(reason, str):
            raise ValueError('Invalid chat input validation result')
        reason = reason.strip()
        if not valid and not reason:
            raise ValueError('Rejected chat input validation result must have a reason')
        return {
            'valid': valid,
            'reason': reason,
        }

    @staticmethod
    def _empty_generated_ticket():
        return {
            'title': '',
            'description': '',
            'type': '',
            'priority': 0,
            'tags': [],
        }

    def _get_ticket_field_context(self, project_uuid):
        field_context = {'type_options': [], 'tags': []}
        if not project_uuid:
            return field_context

        try:
            metadata = self.app.seadb_service.get_base_metadata(project_uuid) or {}
            table_name = SchemaTables.TICKETS.table_name()
            table_meta = next(
                (
                    table for table in metadata.get('tables', [])
                    if table.get('name') == table_name
                ),
                None,
            )
            type_column_name = SchemaTables.TICKETS.column.type.name
            type_column = next(
                (
                    column for column in (table_meta or {}).get('columns', [])
                    if column.get('name') == type_column_name
                ),
                None,
            )
            type_options = []
            seen_type_names = set()
            for option in ((type_column or {}).get('data') or {}).get('options') or []:
                name = str(option.get('name') or '').strip()
                option_id = str(option.get('id') or '').strip()
                name_key = name.casefold()
                if not name or not option_id or name_key in seen_type_names:
                    continue
                seen_type_names.add(name_key)
                type_options.append({'id': option_id, 'name': name})
            field_context['type_options'] = type_options
        except Exception as e:
            logger.warning('Failed to load ticket type options. error: %s', e)

        try:
            tag_table = SchemaTables.TAG.table_name()
            name_column = SchemaTables.TAG.column.name.name
            description_column = SchemaTables.TAG.column.description.name
            sql = (
                f"SELECT `_pk`, `{name_column}`, `{description_column}` "
                f"FROM `{tag_table}` LIMIT 0, 500"
            )
            rows = (
                self.app.seadb_service.query_rows(project_uuid, sql, convert_keys=True) or {}
            ).get('results') or []
            tags = []
            seen_tag_names = set()
            for row in rows:
                if not isinstance(row, dict):
                    continue
                name = str(row.get(name_column) or '').strip()
                name_key = name.casefold()
                try:
                    tag_id = int(row.get('_pk'))
                except (TypeError, ValueError):
                    continue
                if not name or name_key in seen_tag_names:
                    continue
                seen_tag_names.add(name_key)
                tags.append({
                    'id': tag_id,
                    'name': name,
                    'description': str(row.get(description_column) or '').strip(),
                })
            field_context['tags'] = tags
        except Exception as e:
            logger.warning('Failed to load ticket tags. error: %s', e)

        return field_context

    @staticmethod
    def _normalize_generated_ticket(result, field_context):
        result = result if isinstance(result, dict) else {}
        field_context = field_context or {}
        type_name = str(result.get('type') or '').strip().casefold()
        type_id = ''
        if type_name:
            for option in field_context.get('type_options') or []:
                if str(option.get('name') or '').strip().casefold() == type_name:
                    type_id = str(option.get('id') or '').strip()
                    break

        tags_by_name = {}
        for tag in field_context.get('tags') or []:
            name_key = str(tag.get('name') or '').strip().casefold()
            if name_key and name_key not in tags_by_name:
                tags_by_name[name_key] = tag.get('id')
        tag_ids = []
        seen_tag_ids = set()
        raw_tags = result.get('tags') if isinstance(result.get('tags'), list) else []
        for raw_tag in raw_tags:
            tag_id = tags_by_name.get(str(raw_tag or '').strip().casefold())
            if not isinstance(tag_id, int) or isinstance(tag_id, bool) or tag_id in seen_tag_ids:
                continue
            seen_tag_ids.add(tag_id)
            tag_ids.append(tag_id)

        raw_priority = result.get('priority')
        try:
            if isinstance(raw_priority, bool):
                raise ValueError
            priority = int(raw_priority)
        except (TypeError, ValueError):
            priority = 0
        priority = min(4, max(0, priority))

        return {
            'title': str(result.get('title') or '').strip(),
            'description': str(result.get('description') or '').strip(),
            'type': type_id,
            'priority': priority,
            'tags': tag_ids,
        }

    def gen_ticket_from_record(self, record_detail, context):
        """
        Generate a ticket draft from the input record.
        type is a tickets type option id and tags are tag row ids.
        """
        try:
            context = context or {}
            project_uuid = context.get('project_uuid')
            target_lang = self._get_project_target_language(project_uuid)
            field_context = self._get_ticket_field_context(project_uuid)
            lang_instruction = (
                f"Output language requirement: You must write the generated ticket in {target_lang}."
            )
            messages = [
                {
                    "role": "system",
                    "content": f"{AI_UTILS_PROMPTS_MAP['generate_ticket']}\n\n{lang_instruction}"
                },
                {
                    "role": "user",
                    "content": UserContentPrompts.generate_ticket(
                        record_detail, target_lang, field_context
                    )
                }
            ]

            llm_client = get_completion_client_by_model_tier(self.app.data_logger, AI_UTILS_TIER.get('generate_ticket', MODEL_REASONING_TIER.LOW.value))

            response = llm_client.completion(
                messages=messages,
                context=context,
                response_format=gen_completion_format(SCHEMAS_MAP['generate_ticket'])
            ).choices[0].message.content

            try:
                result = json_repair.loads(response, strict=True)
            except Exception as e:
                logger.exception(f'Failure to parse ticket result: {e}')
                return self._empty_generated_ticket()

            return self._normalize_generated_ticket(result, field_context)

        except Exception as e:
            logger.error(f"Error generating ticket: {str(e)}")
            return self._empty_generated_ticket()

    def gen_kb_record_from_ticket(self, ticket_title, ticket_content, ticket_comments, context):
        """
        Generate a knowledge base record (kb_title, kb_content) from a ticket.
        """
        try:
            context = context or {}
            target_lang = self._get_project_target_language(context.get('project_uuid'))
            lang_instruction = (
                f"Output language requirement: You must write the generated knowledge base record in {target_lang}."
            )
            messages = [
                {
                    'role': 'system',
                    'content': f"{AI_UTILS_PROMPTS_MAP['generate_kb_record_from_ticket']}\n\n{lang_instruction}"
                },
                {
                    'role': 'user',
                    'content': UserContentPrompts.generate_kb_record_from_ticket(ticket_title, ticket_content, ticket_comments, target_lang)
                }
            ]

            llm_client = get_completion_client_by_model_tier(
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

    def generate_chat_title(self, query, ai_reply, context):
        try:
            context = context or {}
            project_uuid = context.get('project_uuid')
            target_lang = self._get_project_target_language(project_uuid)
            lang_instruction = (
                f"Output language requirement: You must write the chat title in {target_lang}."
            )
            messages = [
                {
                    "role": "system",
                    "content": f"{AI_UTILS_PROMPTS_MAP['generate_chat_title']}\n\n{lang_instruction}"
                },
                {
                    "role": "user",
                    "content": UserContentPrompts.generate_chat_title(query, ai_reply, target_lang)
                }
            ]

            llm_client = get_completion_client_by_model_tier(
                self.app.data_logger,
                AI_UTILS_TIER.get('generate_chat_title', MODEL_REASONING_TIER.LOW.value)
            )

            response = llm_client.completion(
                messages=messages,
                context=context,
                response_format=gen_completion_format(SCHEMAS_MAP['generate_chat_title'])
            ).choices[0].message.content

            try:
                result = json_repair.loads(response, strict=True)
            except Exception as e:
                logger.exception(f'Failure to parse chat title result: {e}')
                return ''

            return (result.get('title') or '').strip()
        except Exception as e:
            logger.error(f"Error generating chat title: {e}")
            return ''

    def generate_summary(self, connection_type, project_uuid,  content, context):
        try:
            system_prompts = GENERATE_SUMMARY_PROMPTS_MAP.get(connection_type, GENERATE_SUMMARY_PROMPTS_MAP['documents'])
            target_lang = self._get_project_target_language(project_uuid)
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
            
            llm_client = get_completion_client_by_model_tier(self.app.data_logger, AI_UTILS_TIER.get('generate_summary', MODEL_REASONING_TIER.LOW.value))

            response = llm_client.completion(
                messages=messages,
                context=context or {},
            )
            return response.choices[0].message.content.strip()
        except Exception as e:
            logger.error(f"Error generating summary: {e}")
            raise

    def rank_records_by_relevance(self, query_record, candidate_records, context):
        try:
            if not candidate_records:
                return [], {
                    'input_tokens': 0,
                    'output_tokens': 0,
                    'total_tokens': 0
                }
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
                if ExtraSourceType.is_valid(candidate.get('type')):
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
                title = candidate.get('title')
                if title:
                    new_candidate['title'] = title
                summary = candidate.get('ai_summary')
                if summary:
                    new_candidate['summary'] = summary
                snippets = candidate.get('snippets')
                if snippets:
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

            llm_client = get_completion_client_by_model_tier(self.app.data_logger, AI_UTILS_TIER.get('rerank_relevance', MODEL_REASONING_TIER.MEDIUM.value))

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
                return [], token_usage

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

    def _rank_search_results(self, query, search_result, context, limit, project_uuid, connection_id_type_map, call_back=None):
        """
        search_results: a map of {
            'connection_id': {
                'record_id': record # 'record_id', i.e., _pk in seadb, _id in seaqa-indexer
            }
        }
        """
        if not search_result:
            return {}, {
                'input_tokens': 0,
                'output_tokens': 0,
                'total_tokens': 0
            }

        # get rerank required data (title, ai_summary)
        time_begin = time.time()
        search_result = get_title_and_ai_summary_of_search_results(
            search_result, self.app.seadb_service, project_uuid, connection_id_type_map,
        )
        time_end = time.time()
        if isinstance(call_back, ChatCallBacker):
            call_back('update_execution_detail', {'Request title and ai_summary': f'time usage: {round(time_end - time_begin, 2)} s'})

        if not search_result:
            return {}, {
                'input_tokens': 0,
                'output_tokens': 0,
                'total_tokens': 0
            }

        # rank relevance
        time_begin = time.time()
        new_records = new_records = [
            record 
            for record_id_record_map in search_result.values() 
            for record in record_id_record_map.values()
        ]
        ranked_record_id_connection_pairs, rerank_token_usage = self.rank_records_by_relevance(query, new_records, context)
        ranked_record_id_connection_pairs = ranked_record_id_connection_pairs[:limit]
        search_result_after_ranked = {}
        to_retrieve = {}
        provided_labels = context.get('_provided_source_labels', {})
        remaining_budget = context.get('_remaining_source_budget', limit)
        for rank, pair in enumerate(ranked_record_id_connection_pairs):
            after_unpack = pair.split(':')
            if len(after_unpack) != 2:
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
            
            conn_results = search_result.get(connection_id)
            if conn_results and record_id in conn_results:
                ranked_group = search_result_after_ranked.setdefault(connection_id, {})
                if record_id in ranked_group:
                    continue
                record = conn_results[record_id]
                record_hash = gen_record_hash(record['type'], connection_id, record_id)
                entry = {'connection_id': connection_id, 'record_id': record_id}
                if record_hash in provided_labels:
                    record = {
                        'notice': 'Document was already provided.', **entry,
                        'reference_id': provided_labels[record_hash],
                    }
                elif remaining_budget <= 0:
                    record = {'notice': 'Document source limit reached.', **entry}
                else:
                    remaining_budget -= 1
                    to_retrieve.setdefault(connection_id, {})[record_id] = record
                record['_search_rank'] = rank
                ranked_group[record_id] = record

        time_end = time.time()
        if isinstance(call_back, ChatCallBacker):
            call_back('update_execution_detail', {'Rerank search records': f'time usage: {round(time_end - time_begin, 2)} s; tokens usage: {rerank_token_usage["total_tokens"]} (↑{rerank_token_usage["input_tokens"]} ↓{rerank_token_usage["output_tokens"]})'})

        if not search_result_after_ranked:
            return {}, rerank_token_usage
        
        # get content of reranked result
        time_begin = time.time()
        content_retriever = ContentRetriever(
            self.app.username_resolver, self.app.seadb_service, project_uuid,
        )
        if to_retrieve:
            content_retriever.retrieve(to_retrieve, connection_id_type_map)
        for connection_id, group in search_result_after_ranked.items():
            for record_id, record in list(group.items()):
                if 'notice' not in record and record_id not in to_retrieve.get(connection_id, {}):
                    del group[record_id]
        time_end = time.time()
        if isinstance(call_back, ChatCallBacker):
            call_back('update_execution_detail', {'Requst whole data': f'time usage: {round(time_end - time_begin, 2)} s'})

        return search_result_after_ranked, rerank_token_usage
    
    def search(self, query, connections, extra_sources, context, limit=100, call_back=None):
        if limit <= 0:
            return [], ''

        connection_ids = []
        connection_id_type_map = {}

        for conn in connections:
            conn_id = conn['id']
            connection_ids.append(conn_id)
            connection_id_type_map[conn_id] = conn['type']

        connection_ids = ','.join([str(connection_id) for connection_id in connection_ids])

        connection_id_type_map.update({
            ExtraSourceType.KNOWLEDGE_BASE.value: ExtraSourceType.KNOWLEDGE_BASE.value,
            ExtraSourceType.TICKET.value: ExtraSourceType.TICKET.value,
            ExtraSourceType.PORTAL_ISSUE.value: ExtraSourceType.PORTAL_ISSUE.value,
        })

        skip_search_record_hashes = context.get('skip_search_record_hashes', [])

        # keywords search data
        time_begin = time.time()
        project_uuid = context['project_uuid']
        records = self.app.seaqa_indexer_api.keyword_search(query, project_uuid, connection_ids, extra_sources)
        search_results = search_records_to_map(records, {}, skip_search_record_hashes)
        time_end = time.time()
        if isinstance(call_back, ChatCallBacker):
            call_back('update_execution_detail', {'Keywords search': f'time usage: {round(time_end - time_begin, 2)} s'})

        # semantic search data
        time_begin = time.time()
        query_vector = self.app.embedding_api.generate(query, context)
        records = self.app.seaqa_indexer_api.vector_search(query_vector, project_uuid, connection_ids, extra_sources)
        search_results = search_records_to_map(records, search_results, skip_search_record_hashes)
        time_end = time.time()
        if isinstance(call_back, ChatCallBacker):
            call_back('update_execution_detail', {'Vector search': f'time usage: {round(time_end - time_begin, 2)} s'})

        # rerank search data
        search_results, rerank_token_usage = self._rank_search_results(query, search_results, context, limit, uuid_str_to_32_chars(project_uuid), connection_id_type_map, call_back)

        # reformat to list
        sources_results = [
            record 
            for sublist in search_results.values()
            for record in sublist.values()
        ]
        sources_results.sort(key=lambda record: record['_search_rank'])
        for record in sources_results:
            del record['_search_rank']
        
        return sources_results, rerank_token_usage
