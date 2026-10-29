import logging
from urllib.parse import quote

from seaqa_ai import config
from seaqa_ai.utils import gen_record_hash
from seaqa_ai.utils.tools import BasicTool
from seaqa_ai.utils.constants import ConnectionType, ExtraSourceType

logger = logging.getLogger(__name__)

_SEARCH_TOOLS_RESULT_FORMAT = """
[
    {
        "label": "<reference_0>",
        "type": "github_issue | discourse_forum | email | ticket | ...",
        "connection_id": "Connection ID, or null for extra sources",
        "record_id": 123,
        "title": "Title",
        "ai_summary": "Search result's summary",
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
        "connection_id": "Connection ID, or null for extra sources",
        "record_id": 456,
        "title": "Title",
        "ai_summary": "Search result's summary",
        "url": "Record URL (if available)"
        // ... Other useful information, e.g., snippets ...
    }
]
"""

class BasicSearchTool(BasicTool):
    MAX_SOURCES_COUNT = 20
    SINGLE_TOOL_MAX_SOURCES_COUNT = 5
    STATUS_FIELDS = (
        'state',
        'state_reason',
        'substate',
        'resolved',
        'linked_ticket',
    )

    @staticmethod
    def _build_project_base_url(context, app, tool_executor):
        cache_key = 'project_base_url'
        if cache_key in tool_executor.cache:
            return tool_executor.cache[cache_key]

        seaqa_web_service_url = (config.SEAQA_WEB_SERVICE_URL or '').strip().rstrip('/')
        project_uuid = context.get('project_uuid')
        if not seaqa_web_service_url or not project_uuid or not getattr(app, 'seaqa_db', None):
            return ''

        project_metadata = app.seaqa_db.get_project_metadata(project_uuid)
        workspace_id = project_metadata.get('workspace_id')
        project_name = project_metadata.get('name')
        if workspace_id is None or not project_name:
            return ''

        project_name = quote(str(project_name), safe='')
        project_base_url = f'{seaqa_web_service_url}/workspace/{workspace_id}/project/{project_name}'
        tool_executor.cache[cache_key] = project_base_url
        return project_base_url

    @staticmethod
    def _build_product_record_url(search_result, project_base_url):
        if not project_base_url:
            return ''

        source_type = search_result.get('type')
        record_id = search_result.get('_id')
        connection_id = search_result.get('connection_id')
        if record_id is None:
            return ''

        if source_type == ExtraSourceType.TICKET.value:
            record_path = f'/tickets/{record_id}/'
        elif source_type == ExtraSourceType.PORTAL_ISSUE.value:
            record_path = f'/portal-issues/{record_id}/'
        elif source_type == ExtraSourceType.KNOWLEDGE_BASE.value:
            record_path = f'/knowledge-base/{record_id}/'
        elif ConnectionType.is_valid(source_type) and connection_id:
            record_path = f'/connections/{connection_id}/records/{record_id}/'
        else:
            return ''

        return f'{project_base_url}{record_path}'

    def _search(self, query, connections, extra_sources, context, app, tool_executor, call_back):
        assert isinstance(query, str), "Your search query must be a string"
        sources_results = tool_executor.cache.get('sources_results', [])
        if not connections and not extra_sources:
            return []

        limit = self.SINGLE_TOOL_MAX_SOURCES_COUNT

        current_search_results, rerank_token_usage = app.ai_services.search(query, connections, extra_sources, context, limit, call_back)

        tool_executor.thought_process.update_last_group_tokens_usage(rerank_token_usage)

        project_base_url = (
            self._build_project_base_url(context, app, tool_executor)
            if context.get('skill_id') == 'issue-research' and not context.get('is_external_portal')
            else ''
        )
        references = {
            gen_record_hash(record['type'], record.get('connection_id'), record['_id']): f'<reference_{index}>'
            for index, record in enumerate(sources_results)
        }
        to_observation = []
        for search_result in current_search_results:
            source_type = search_result['type']
            connection_id = source_type if ExtraSourceType.is_valid(source_type) else search_result['connection_id']
            record_hash = gen_record_hash(source_type, search_result.get('connection_id'), search_result['_id'])
            if record_hash in references:
                to_observation.append({
                    'notice': 'Document was already provided.',
                    'connection_id': connection_id,
                    'record_id': search_result['_id'],
                    'reference_id': references[record_hash],
                })
                continue
            if len(sources_results) >= self.MAX_SOURCES_COUNT:
                continue
            reference_id = f'<reference_{len(sources_results)}>'
            doc = {
                'label': reference_id,
                'type': search_result['type'],
                'connection_id': None if ExtraSourceType.is_valid(search_result['type']) else search_result['connection_id'],
                'record_id': search_result['_id'],
                'title': search_result['title'],
                'ai_summary': search_result.get('ai_summary', ''),
                'modified_time': search_result['modified_time'],
            }
            if 'url' in search_result:
                doc['url'] = search_result['url']
            for field in self.STATUS_FIELDS:
                value = search_result.get(field)
                if value is not None:
                    doc[field] = value
            inner_url = self._build_product_record_url(search_result, project_base_url)
            if inner_url:
                doc['inner_url'] = inner_url
                search_result['inner_url'] = inner_url
            if 'snippets' in search_result:
                doc['snippets'] = search_result['snippets']
            to_observation.append(doc)
            references[record_hash] = reference_id
            sources_results.append(search_result)

        tool_executor.cache['sources_results'] = sources_results

        return to_observation

class DocumentsSearch(BasicSearchTool):
    tool = {
        'type': 'function',
        'name': 'documents_search',
        'description': f'Search technical documents by a short natural-language query. Results are ordered by relevance and contain summaries and metadata, NOT full content. Select useful identities and call get_records if details are needed. {_SEARCH_TOOLS_RESULT_FORMAT}. Do not make random or redundant searches.',
        'parameters':{
            'type': 'object',
            'properties': {
                'query': {
                    'type': 'string',
                    'description': 'The query is a short sentence (not keywords) related to the documents. For example, when a user asks "what\'s seafile", you should use \'introduction of seafile\' instead of the simple keyword \'seafile\' as `query`.'
                },
            },
            'required': ['query'],
            'additionalProperties': False,
        },
        'strict': True,
    }

    def execute(self, query, context, app, tool_executor, call_back):
        extra_sources = ExtraSourceType.get_document_extra_source_types()
        if context.get('extra_sources') is not None:
            extra_sources = [s for s in context['extra_sources'] if s in extra_sources]
        return self._search(query, context.get('document_connections', []), extra_sources, context, app, tool_executor, call_back)
class IssuesSearch(BasicSearchTool):
    tool = {
        'type': 'function',
        'name': 'issues_search',
        'description': (
            f'This is a search tool for relative issues. You can use a query (a short sentence) '
            f'to find relative issues and past solutions. Relevance decreases from beginning to end. '
            f'Results may include structured status fields (e.g. state/resolved/linked_ticket_state) '
            f'and summaries, but NOT full content. Read useful candidates with get_records '
            f'before relying on detailed solutions or applicability. '
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
            'required': ['query'],
            'additionalProperties': False,
        },
        'strict': True,
    }

    def execute(self, query, context, app, tool_executor, call_back):
        extra_sources = ExtraSourceType.get_issue_extra_source_types()
        if context.get('extra_sources') is not None:
            extra_sources = [s for s in context['extra_sources'] if s in extra_sources]
        return self._search(query, context.get('issue_connections', []), extra_sources, context, app, tool_executor, call_back)

class TasksSearch(BasicSearchTool):
    tool = {
        'type': 'function',
        'name': 'tasks_search',
        'description': (
            f'This is a search tool for related task records (including but not limited to feature development handling tasks and requirement design tasks). Use it when the user explicitly asks '
            f'to find related tasks, matching tasks, task records, or corresponding tasks for a described problem. '
            f'Relevance decreases from beginning to end. {_SEARCH_TOOLS_RESULT_FORMAT}. '
            'Results contain summaries and metadata, NOT full content. Use get_records for task details. '
            'Use this tool based only on the user instruction type; do not include it in the automatic '
            'documents_search/issues_search priority or fallback order. '
            '**Note: Not every sentence can be searched for relevant results. '
            'Regardless of whether there are results or not, you should not make random searches.**'
        ),
        'parameters':{
            'type': 'object',
            'properties': {
                'query': {
                    'type': 'string',
                    'description': 'The query is a short sentence describing the problem or work item for which related tasks should be found.'
                },
            },
            'required': ['query'],
            'additionalProperties': False,
        },
        'strict': True,
    }

    def execute(self, query, context, app, tool_executor, call_back):
        extra_sources = ExtraSourceType.get_task_extra_source_types()
        if context.get('extra_sources') is not None:
            extra_sources = [s for s in context['extra_sources'] if s in extra_sources]
        return self._search(query, context.get('task_connections', []), extra_sources, context, app, tool_executor, call_back)
