import logging
from seaqa_ai.agent.tools import BasicTool

logger = logging.getLogger(__name__)

_SEARCH_TOOLS_RESULT_FORMAT = """The result of this tool has the below format (ranked by keyword relevance):
===== Reference 0 ===== Title: <title of Reference 0> =====
<Reference 0's content>


===== Reference 1 ===== Title: <title of Reference 1> =====
<Reference 1's content>
"""

class BasicSearchTool(BasicTool):
    MAX_SOURCES_COUNT = 10
    SINGLE_TOOL_MAX_SOURCES_COUNT = 5

    def _search(self, query, connections, context, tool_executor):
        assert isinstance(query, str), "Your search query must be a string"
        # Get current task search history
        sources_results = tool_executor.cache.get('sources_results', [])
        existed_search_records_num = len(sources_results)

        if not connections or existed_search_records_num >= self.MAX_SOURCES_COUNT:
            return {
                'to_observation': '',
                'to_db': []
            }

        limit = self.MAX_SOURCES_COUNT - existed_search_records_num
        if limit > self.SINGLE_TOOL_MAX_SOURCES_COUNT:
            limit = self.SINGLE_TOOL_MAX_SOURCES_COUNT

        current_search_results = self.app.ai_utils.search(query, connections, context, limit)

        to_observation = []
        for i, search_result in enumerate(current_search_results, existed_search_records_num):
            doc = {
                'label': f'<reference_{i}>',
                'title': search_result['title'],
                'content': search_result['content'],
            }
            if 'url' in search_result:
                doc['url'] = search_result['url']
            to_observation.append(doc)
            del search_result['content']

        tool_executor.cache.update({
            'sources_results': sources_results + current_search_results
        })

        # Format the retrieved documents for readability
        return {
            'to_observation': to_observation,
            'to_db': current_search_results
        }

class documents_search(BasicSearchTool):
    tool = {
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
    }

    def execute(self, query, context, tool_executor):
        return self._search(query, context['document_connections'], context, tool_executor)
    
class issues_search(BasicSearchTool):
    tool = {
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

    def execute(self, query, context, tool_executor):
        return self._search(query, context['issue_connections'], context, tool_executor)
