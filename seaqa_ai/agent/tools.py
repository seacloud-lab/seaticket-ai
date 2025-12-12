import logging
from smolagents import Tool

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
                        'description': 'The query is a short sentence related to the documents.'
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
                        'description': 'The query is a short sentence related to the issues'
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

        if len(sources_results) > self.MAX_SOURCES_COUNT:
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
        ranked_records = self.app.ai_utils.rank_issues_by_relevance(query, records, context)

        for doc in ranked_records:
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

        if len(sources_results) > self.MAX_SOURCES_COUNT:
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
            for connection_id in context['issue_connection_ids']
        ])
        project_uuid = context['project_uuid']

        records = self.app.seaqa_indexer_api.related_records_search(query, project_uuid, connection_ids)
        ranked_records = self.app.ai_utils.rank_issues_by_relevance(query, records, context)[:self.MAX_SOURCES_COUNT - len(sources_results)]

        for doc in ranked_records:
            doc.update({
                'title': doc['title'] if doc.get('title') else doc.get('filename')
            })
            to_observation += f"===== Reference {len(to_tools_shared_vars['sources_results'])} ===== Title: {doc.get('title', '')} =====\n{doc.get('content', '')}\n\n"
            doc['content_preview'] = self._get_search_result_preview(doc['content'])
            del doc['content'] # discard content, only save metadata
            to_tools_shared_vars['sources_results'].append(doc)
            to_db.append(doc)

        to_tools_shared_vars['sources_results'] = sources_results
        tool_executor.tool_shared_vars.update(to_tools_shared_vars)

        # Format the retrieved documents for readability
        return {
            'to_observation': to_observation,
            'to_db': to_db
        }
