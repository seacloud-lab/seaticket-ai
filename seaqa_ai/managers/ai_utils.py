import logging
import json_repair
import uuid
from urllib.parse import urlparse
from seaqa_ai.utils import gen_seadb_table_name, convert_to_json_code_block
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
                if key in candidate_key_set and key not in seen_keys:
                    validated_keys.append(key)
                    seen_keys.add(key)
            return validated_keys

        except Exception as e:
            logger.error(f"Error ranking related records: {e}")
            raise

    def documents_search(self, query, connection_ids, context, limit=100, current_ref_num=0, preview=False):
        if limit <= 0:
            return [], ''
        if isinstance(connection_ids, list):
            connection_ids = ','.join([
                str(connection_id)
                for connection_id in connection_ids
            ])
        project_uuid = context['project_uuid']
        records = self.app.seaqa_indexer_api.keywords_search(query, project_uuid, connection_ids, context['username'])
        ranked_record_id_connection_pairs = self.rank_issues_by_relevance(query, records, context)[:limit]
        search_results = []
        references_docs = ''
        for doc in [record for record in records if f"{record['_id']}:{record.get('connection_id', 'unknown')}" in ranked_record_id_connection_pairs]:
            current_ref_num += 1
            doc.update({
                'title': doc['title'] if doc.get('title') else doc.get('filename')
            })
            references_docs += f"===== Reference {current_ref_num} ===== Title: {doc.get('title', '')} =====\n{doc.get('content', '')}\n\n"
            if preview:
                doc['content_preview'] = doc['content'][:100]
                del doc['content']
            search_results.append(doc)
        return search_results, references_docs
    
    def issues_search(self, query, connections, context, limit=100, current_ref_num=0, preview=False):
        if limit <= 0:
            return [], ''
        project_uuid = context['project_uuid']
        connection_id_type_map = {}
        connection_id_config_map = {}
        for connection in connections:
            connection_id_type_map[connection['id']] = connection['type']
            connection_id_config_map[connection['id']] = connection['config']
        connection_ids = list(connection_id_type_map.keys())

        query_vector = self.app.ai_utils.llm_api.generate_embeddings(query, context)[0]['embedding']
        summary_records = self.app.seaqa_indexer_api.vector_search(query_vector, project_uuid, connection_ids)
        record_id_score_map = {
            record['pk']: record['score']
            for record in summary_records
        }
        ranked_record_id_connection_pairs = self.rank_issues_by_relevance(query, summary_records, context)[:limit]
        
        project_uuid = str(uuid.UUID(project_uuid))
        search_results = []
        references_docs = ''
        
        for ranked_record_id_connection_pair in ranked_record_id_connection_pairs:
            if len((after_unpack := ranked_record_id_connection_pair.split(':'))) != 2:
                continue
            record_id, connection_id = after_unpack
            record_id = int(record_id)
            connection_id = int(connection_id)
            if connection_id not in connection_id_type_map:
                continue
            elif connection_id_type_map[connection_id] == 'discourse_forum':
                current_ref_num += 1
                connection_table_name, comments_table_name = gen_seadb_table_name(connection_id_type_map[connection_id], connection_id)
                record, comments = self.app.seadb_api.get_record_and_comments(project_uuid, record_id, connection_table_name, comments_table_name=comments_table_name, topic_id_field_name='topic_id')
                whole_data = {
                    'type': 'discourse_forum',
                    '_id': record_id,
                    'topic_id': record['topic_id'],
                    'connection_id': connection_id,
                    'url': connection_id_config_map[connection_id].get('url'),
                    'score': record_id_score_map[record_id],
                    'title': record['title'],
                    'slug': record['slug'],
                    'created_time': record['created_time'],
                    'modified_time': record['modified_time'],
                    'comments': [
                        {
                            'author': comment['author'],
                            'post_number': comment['post_number'],
                            'content': comment['content']
                        }
                        for comment in comments
                    ]
                }
                references_docs += f"===== Reference {current_ref_num} ===== Title: {whole_data['title']} =====\n{convert_to_json_code_block(whole_data)}\n\n"
                if preview:
                    whole_data['content_preview'] = record['ai_summary']
                    del whole_data['comments']
                search_results.append(whole_data)
            elif connection_id_type_map[connection_id] == 'github_issue':
                current_ref_num += 1
                connection_table_name, comments_table_name = gen_seadb_table_name(connection_id_type_map[connection_id], connection_id)
                record, comments = self.app.seadb_api.get_record_and_comments(project_uuid, record_id, connection_table_name, comments_table_name=comments_table_name, topic_id_field_name='issue_id', has_deleted_field=True)

                # url
                server_url = connection_id_config_map[connection_id].get('repository')
                url_parsed = urlparse(server_url)
                base_url = url_parsed.scheme + "://" + url_parsed.netloc
                parts = url_parsed.path.strip("/").split("/")
                repo_owner, repo_name = parts[0], parts[1]
                url = f'{base_url}/{repo_owner}/{repo_name}/issues/' + str(record['issue_number'])

                whole_data = {
                    'type': 'github_issue',
                    '_id': record_id,
                    'connection_id': connection_id,
                    'content': record['content'],
                    'url': url,
                    'score': record_id_score_map[record_id],
                    'title': record['title'],
                    'repo_owner': record['repo_owner'],
                    'repo_name': record['repo_name'],
                    'issue_number': record['issue_number'],
                    'state': record['state'],
                    'comments': [
                        {
                            'author': comment['author'],
                            'content': comment['content']
                        }
                        for comment in comments
                    ]
                }
                references_docs += f"===== Reference {current_ref_num} ===== Title: {whole_data['title']} =====\n{convert_to_json_code_block(whole_data)}\n\n"
                if preview:
                    whole_data['content_preview'] = whole_data['content'][:100]
                    del whole_data['comments']
                    del whole_data['content']
                search_results.append(whole_data)
            elif connection_id_type_map[connection_id] == 'email':
                current_ref_num += 1
                connection_table_name, _ = gen_seadb_table_name(connection_id_type_map[connection_id], connection_id)
                record, _ = self.app.seadb_api.get_record_and_comments(project_uuid, record_id, connection_table_name)
                whole_data = {
                    'type': 'email',
                    '_id': record_id,
                    'connection_id': connection_id,
                    'content': record['content'],
                    'score': record_id_score_map[record_id],
                    'title': record['title'],
                }
                references_docs += f"===== Reference {current_ref_num} ===== Title: {whole_data['title']} =====\n{convert_to_json_code_block(whole_data)}\n\n"
                if preview:
                    whole_data['content_preview'] = whole_data['content'][:100]
                    del whole_data['content']
                search_results.append(whole_data)
            else:
                continue
        return search_results, references_docs
