import logging
from seaqa_ai.utils.tools import BasicTool
from seaqa_ai.utils.constants import ExtraSourceType

logger = logging.getLogger(__name__)

_SEARCH_TOOLS_RESULT_FORMAT = """
[
    {
        "label": "<reference_0>",
        "type": "github_issue | discourse_forum | email | ticket | ...",
        "title": "Title",
        "content": "Search result's content",
        "url": "Record URL (if available)",
        "state": "open/closed (when available)",
        "state_reason": "state reason for GitHub issue (when available)",
        "resolved": true,
        "linked_ticket": 123,
        "linked_ticket_state": "open/closed"
        // ... Other useful information, e.g., snippets ...
    }, {
        "label": "<reference_1>",
        "type": "github_issue | discourse_forum | email | ticket | ...",
        "title": "Title",
        "content": "Search result's content",
        "url": "Record URL (if available)"
        // ... Other useful information, e.g., snippets ...
    }
]
"""

class BasicSearchTool(BasicTool):
    MAX_SOURCES_COUNT = 10
    SINGLE_TOOL_MAX_SOURCES_COUNT = 5

    def _search(self, query, connections, extra_sources, context, app, tool_executor, call_back):
        assert isinstance(query, str), "Your search query must be a string"
        sources_results = tool_executor.cache.get('sources_results', [])
        existed_search_records_num = len(sources_results)

        if not connections and not extra_sources or existed_search_records_num >= self.MAX_SOURCES_COUNT:
            return []

        limit = self.MAX_SOURCES_COUNT - existed_search_records_num
        if limit > self.SINGLE_TOOL_MAX_SOURCES_COUNT:
            limit = self.SINGLE_TOOL_MAX_SOURCES_COUNT

        current_search_results, rerank_token_usage = app.ai_services.search(query, connections, extra_sources, context, limit, call_back)

        tool_executor.thought_process.update_last_group_tokens_usage(rerank_token_usage)

        to_observation = []
        for i, search_result in enumerate(current_search_results, existed_search_records_num):
            doc = {
                'label': f'<reference_{i}>',
                'type': search_result['type'],
                'title': search_result['title'],
                'content': search_result['content'],
                'modified_time': search_result['modified_time'],
            }
            if 'url' in search_result:
                doc['url'] = search_result['url']
            if 'snippets' in search_result:
                doc['snippets'] = search_result['snippets']
            to_observation.append(doc)

        tool_executor.cache.update({
            'sources_results': sources_results + current_search_results
        })

        return to_observation

class DocumentsSearch(BasicSearchTool):
    tool = {
        'type': 'function',
        'name': 'documents_search',
        'description': f'This is a search tool for technical documents. You can use a query (a short sentence) to find relative documents. The relevance will decrease from the beginning to the end. {_SEARCH_TOOLS_RESULT_FORMAT}. **Note: Not every sentence can be searched for relevant results. Regardless of whether there are results or not, you should not make random searches.**',
        'parameters':{
            'type': 'object',
            'properties': {
                'query': {
                    'type': 'string',
                    'description': 'The query is a short sentence (not keywords) related to the documents. For example, when a user asks "what\'s seafile", you should use \'introduction of seafile\' instead of the simple keyword \'seafile\' as `query`.'
                },
            },
            'required': ['query']
        },
        'strict': True,
    }

    def execute(self, query, context, app, tool_executor, call_back):
        extra_sources = ExtraSourceType.get_document_extra_source_types()
        if context.get('extra_sources') is not None:
            extra_sources = [s for s in context['extra_sources'] if s in extra_sources]
        return self._search(query, context['document_connections'], extra_sources, context, app, tool_executor, call_back)
class IssuesSearch(BasicSearchTool):
    tool = {
        'type': 'function',
        'name': 'issues_search',
        'description': (
            f'This is a search tool for relative issues. You can use a query (a short sentence) '
            f'to find relative issues and past solutions. Relevance decreases from beginning to end. '
            f'Results may include structured status fields (e.g. state/resolved/linked_ticket_state) '
            f'when available. {_SEARCH_TOOLS_RESULT_FORMAT}. '
            '**Note: Not every sentence can be searched for relevant results. '
            'Regardless of whether there are results or not, you should not make random searches.**'
        ),
        'parameters':{
            'type': 'object',
            'properties': {
                'query': {
                    'type': 'string',
                    'description': 'The query is a short sentence related to the issues. A short sentence means, for example, when a user reports a bug or issue, you should use a single sentence instead of several keywords.'
                },
            },
            'required': ['query']
        },
        'strict': True,
    }

    def execute(self, query, context, app, tool_executor, call_back):
        extra_sources = ExtraSourceType.get_issue_extra_source_types()
        if context.get('extra_sources') is not None:
            extra_sources = [s for s in context['extra_sources'] if s in extra_sources]
        return self._search(query, context['issue_connections'], extra_sources, context, app, tool_executor, call_back)
