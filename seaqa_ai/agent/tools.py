import logging
import uuid
from urllib.parse import urlparse
from smolagents import Tool
from seaqa_ai.utils import gen_seadb_table_name, convert_to_json_code_block

logger = logging.getLogger(__name__)

_SEARCH_TOOLS_RESULT_FORMAT = """The result of this tool has the below format (ranked by keyword relevance):
===== Reference 0 ===== Title: <title of Reference 0> =====
<Reference 0's content>


===== Reference 1 ===== Title: <title of Reference 1> =====
<Reference 1's content>
"""

Tools = {
    'documents_search': {
        'type': 'function',
        'function': {
            'name': 'documents_search',
            'description': f'This is a search tool for technical documents. You can use a query (a short sentence) to find relative documents. The relevance will decrease from the beginning to the end. {_SEARCH_TOOLS_RESULT_FORMAT}. **Note: Not every sentence can be searched for relevant results. Regardless of whether there are results or not, you should not make random searches.**',
            'parameters':{
                'type': 'object',
                'properties': {
                    'query': {
                        'type': 'string',
                        'description': 'The query is a short sentence (not keywords) related to the documents. For example, when a user asks "what\'s seafile", you should use \'introduction of seafile\' instead of the simple keyword \'seafile\' as `query`.'
                    }
                },
                'required': ['query']
            }
        }
    }, 
    'issues_search': {
        'type': 'function',
        'function': {
            'name': 'issues_search',
            'description': f'This is a search tool for relative issues. You can use a query (a short sentence) to find relative issues and a past solution to this problem. The relevance will decrease from the beginning to the end. {_SEARCH_TOOLS_RESULT_FORMAT}. **Note: Not every sentence can be searched for relevant results. Regardless of whether there are results or not, you should not make random searches.**',
            'parameters':{
                'type': 'object',
                'properties': {
                    'query': {
                        'type': 'string',
                        'description': 'The query is a short sentence related to the issues. A short sentence means, for example, when a user reports a bug or issue, you should use a single sentence instead of several keywords.'
                    }
                },
                'required': ['query']
            }
        }
    }
}

class FinalAnswerTool(Tool):
    name = "final_answer"
    description = """Provides a final answer to the given problem. This tool requires two mandatory parameters: `answer` and `reference_ids`. In addition to the parameter descriptions, the following conditions must also be met:
- If you have ever used a tool and the tool's result has "===== Reference <id> =====" (where `<id>` is an integer) and you found this result helpful for your answer, you MUST add the `<id>` to the `reference_ids` list (ensuring no duplicates).
- If you have not used any tools, the tool result does not have "===== Reference <id> =====", or the tool result is not helpful for the answer, then the `reference_ids` MUST be an empty list.
"""
    inputs = {
        "answer": {
            "type": "string", 
            "description": """A string containing your main response. 
- It must be formatted using Markdown, elegantly and logically organized with elements like paragraphs, lists, tables, or code blocks to enhance readability. 
- For each 1-based index in `reference_ids` (i.e., `range(1, 1 + len(reference_ids))`), MUST add at least one corresponding Markdown label `[Reference n]`, where `n` is a 1-based index (e.g., the first reference uses `[Reference 1]`, the second uses `[Reference 2]`, and so on).
- The labels must appear sequentially, meaning the i-th ID in the `reference_ids` list corresponds to the `[Reference i+1]` label.
- Please ensure that `answer` contains at least one reference label for each of the indexes in the `reference_ids` list (i.e., `[Reference 1]` to `[Reference len(reference_ids)]`). There must be no extra or missing labels.
"""
        },
        "reference_ids": {
            "type": "array", 
            "items": {
                "type": "integer"
            },
            "description": "A list of integers containing reference IDs extracted from tool results (must not contain duplicates). If no references are used, this must be an empty list. For each 1-based index in `reference_ids` (i.e., `range(1, 1 + len(reference_ids))`), MUST add at least one corresponding Markdown label `[Reference n]` in argument `answer`, where `n` is a 1-based index (e.g., the first reference uses `[Reference 1]`, the second uses `[Reference 2]`, and so on, i.e., `[Reference n]` means `reference_ids[n-1]`)"
        }
    }
    output_type = "any"

    def forward(self, answer, reference_ids):
        return {
            'answer': answer,
            'reference_ids': reference_ids
        }
    
class BasicTools(object):
    def __init__(self, app):
        self.app = app
        self._initialize_tool_name_func_map()
        assert isinstance(self.tool_name_func_map, dict), "Property tool_name_func_map must be a dict after initializing"

    def _initial_tool_name_func_map(self):
        raise NotImplementedError()
    
    def register(self, tool_executor, tool_name, **constant_kwargs):
        if tool_name in self.tool_name_func_map:
            tool_executor.register(self.tool_name_func_map[tool_name], tool_name, **constant_kwargs)
    
    def registers(self, tool_executor, **constant_kwargs):
        for tool_name, tool_func in self.tool_name_func_map.items():
            tool_executor.register(tool_func, Tools[tool_name], **constant_kwargs)

class SearchTools(BasicTools):
    MAX_SOURCES_COUNT = 10
    SINGLE_TOOL_MAX_SOURCES_COUNT = 5
    def _initialize_tool_name_func_map(self):
        self.tool_name_func_map = {
            'documents_search': self.documents_search,
            'issues_search': self.issues_search
        }

    def _get_search_result_preview(self, doc_content):
        # reserved interface
        return doc_content[:100]

    def documents_search(self, query, context, tool_executor):
        """Execute the retrieval based on the provided query."""
        assert isinstance(query, str), "Your search query must be a string"
        # Get current task search history
        sources_results = tool_executor.tool_shared_vars.get('sources_results', [])

        if not context['document_connection_ids'] or len(sources_results) > self.MAX_SOURCES_COUNT:
            return {
                'to_observation': '',
                'to_db': []
            }
        
        to_observation = ''
        to_tools_shared_vars = {
            'sources_results': sources_results
        }
        to_db = []
        connection_ids = ','.join([
            str(connection_id)
            for connection_id in context['document_connection_ids']
        ])
        project_uuid = context['project_uuid']
        records = self.app.seaqa_indexer_api.noramal_search(query, project_uuid, connection_ids)
        ranked_record_id_connection_pairs = self.app.ai_utils.rank_search_results_by_relevance(query, records, context)

        for doc in [record for record in records if f"{record['_id']}:{record['connection_id']}" in ranked_record_id_connection_pairs]:
            doc.update({
                'title': doc['title'] if doc.get('title') else doc.get('filename')
            })
            to_observation += f"===== Reference {len(to_tools_shared_vars['sources_results'])} ===== Title: {doc.get('title', '')} =====\n{doc.get('content', '')}\n\n"
            doc['content_preview'] = self._get_search_result_preview(doc['content'])
            del doc['content'] # discard content, only save metadata
            to_tools_shared_vars['sources_results'].append(doc)
            to_db.append(doc)

        tool_executor.tool_shared_vars.update(to_tools_shared_vars)

        # Format the retrieved documents for readability
        return {
            'to_observation': to_observation,
            'to_db': to_db
        }

    def issues_search(self, query, context, tool_executor):
        """Execute the retrieval based on the provided query."""
        assert isinstance(query, str), "Your search query must be a string"

        # Get current task search history
        sources_results = tool_executor.tool_shared_vars.get('sources_results', [])

        if not context['issue_connections'] or len(sources_results) > self.MAX_SOURCES_COUNT:
            return {
                'to_observation': '',
                'to_db': []
            }
        
        to_observation = ''
        to_tools_shared_vars = {
            'sources_results': sources_results
        }
        to_db = []

        project_uuid = context['project_uuid']
        connection_id_type_map = {}
        connection_id_config_map = {}
        for connection in context['issue_connections']:
            connection_id_type_map[connection['id']] = connection['type']
            connection_id_config_map[connection['id']] = connection['config']
        connection_ids = list(connection_id_type_map.keys())

        query_vector = self.app.ai_utils.llm_api.generate_embeddings(query, context)[0]['embedding']
        summary_records = self.app.seaqa_indexer_api.related_records_search(query_vector, project_uuid, connection_ids)
        record_id_score_map = {
            record['pk']: record['score']
            for record in summary_records
        }
        ranked_record_id_connection_pairs = self.app.ai_utils.rank_search_results_by_relevance(query, summary_records, context)[:self.MAX_SOURCES_COUNT - len(sources_results)]
        
        project_uuid = str(uuid.UUID(project_uuid))
        
        for ranked_record_id_connection_pair in ranked_record_id_connection_pairs:
            if len((after_unpack := ranked_record_id_connection_pair.split(':'))) != 2:
                continue
            record_id, connection_id = after_unpack
            record_id = int(record_id)
            connection_id = int(connection_id)
            if connection_id not in connection_id_type_map:
                continue
            elif connection_id_type_map[connection_id] == 'discourse_forum':
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
                to_observation += f"===== Reference {len(to_tools_shared_vars['sources_results'])} ===== Title: {whole_data['title']} =====\n{convert_to_json_code_block(whole_data)}\n\n"
                whole_data['content_preview'] = record['ai_summary']
                del whole_data['comments']
            elif connection_id_type_map[connection_id] == 'github_issue':
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
                to_observation += f"===== Reference {len(to_tools_shared_vars['sources_results'])} ===== Title: {whole_data['title']} =====\n{convert_to_json_code_block(whole_data)}\n\n"
                whole_data['content_preview'] = self._get_search_result_preview(whole_data['content'])
                del whole_data['comments']
                del whole_data['content']
            elif connection_id_type_map[connection_id] == 'email':
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
                to_observation += f"===== Reference {len(to_tools_shared_vars['sources_results'])} ===== Title: {whole_data['title']} =====\n{convert_to_json_code_block(whole_data)}\n\n"
                whole_data['content_preview'] = self._get_search_result_preview(whole_data['content'])
                del whole_data['content']
            else:
                continue
            to_tools_shared_vars['sources_results'].append(whole_data)
            to_db.append(whole_data)

        tool_executor.tool_shared_vars.update(to_tools_shared_vars)

        # Format the retrieved documents for readability
        return {
            'to_observation': to_observation,
            'to_db': to_db
        }
