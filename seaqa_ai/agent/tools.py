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
    
    def _search(self, query, connections, context, tool_executor):
        assert isinstance(query, str), "Your search query must be a string"
        # Get current task search history
        sources_results = tool_executor.tool_shared_vars.get('sources_results', [])

        if not connections or len(sources_results) >= self.MAX_SOURCES_COUNT:
            return {
                'to_observation': '',
                'to_db': []
            }

        limit = self.MAX_SOURCES_COUNT - len(sources_results)
        if limit > self.SINGLE_TOOL_MAX_SOURCES_COUNT:
            limit = self.SINGLE_TOOL_MAX_SOURCES_COUNT


        current_search_results, search_results_full_docs = self.app.ai_utils.search(query, connections, context, limit, len(sources_results))

        tool_executor.tool_shared_vars.update({
            'sources_results': sources_results + current_search_results
        })

        # Format the retrieved documents for readability
        return {
            'to_observation': search_results_full_docs,
            'to_db': current_search_results
        }


    def documents_search(self, query, context, tool_executor):
        return self._search(query, context['document_connections'], context, tool_executor)
       

    def issues_search(self, query, context, tool_executor):
        return self._search(query, context['issue_connections'], context, tool_executor)
