"""Stand-ins for the model client, search, and usage logging."""

from types import SimpleNamespace

from seaqa_ai.utils.constants import ExtraSourceType
from seaqa_ai.utils.llm_api import ResponsesResult


ISSUES_SEARCH = 'issues_search'
DOCUMENTS_SEARCH = 'documents_search'


class NullDataLogger:
    def log_data(self, *args, **kwargs):
        return None


class FakeAIServices:
    """Return pre-recorded search hits per search tool and a fixed ticket draft.

    ``results`` maps a search tool name to its hits, for example
    ``{'issues_search': [...], 'documents_search': [...]}``. Every call is
    recorded in ``calls`` as ``{'tool': ..., 'query': ...}``.
    """

    def __init__(self, results=None):
        self.results = results or {}
        self.calls = []

    @property
    def queries(self):
        return [call['query'] for call in self.calls]

    @staticmethod
    def classify(connections, extra_sources, context):
        """Tell which search tool is calling.

        IssuesSearch passes issue extra sources (ticket, portal_issue) and
        DocumentsSearch passes document extra sources (knowledge_base); the
        context can filter those out, so fall back to the connection ids.
        """
        extras = set(extra_sources or [])
        if extras & set(ExtraSourceType.get_issue_extra_source_types()):
            return ISSUES_SEARCH
        if extras & set(ExtraSourceType.get_document_extra_source_types()):
            return DOCUMENTS_SEARCH
        connection_ids = {
            item.get('id') for item in connections or [] if isinstance(item, dict)
        }
        issue_ids = {
            item.get('id')
            for item in (context or {}).get('issue_connections', [])
            if isinstance(item, dict)
        }
        return ISSUES_SEARCH if connection_ids & issue_ids else DOCUMENTS_SEARCH

    def search(self, query, connections, extra_sources, context, limit, call_back):
        tool = self.classify(connections, extra_sources, context)
        self.calls.append({'tool': tool, 'query': query})
        rows = [dict(row) for row in self.results.get(tool) or []]
        return rows[:limit], {}

    def gen_ticket_from_record(self, detail, context):
        return {
            'title': 'Draft ticket',
            'description': 'Draft description',
            'type': 'Bug',
            'priority': 0,
            'tags': [],
        }


class FakeSeaDB:
    """In-memory SeaDB that serves records and logs every write in order.

    ``tables`` maps a table name to ``{pk: row}``. Records are read the way
    ``GetRecord`` reads them: a row holds the opening body under ``content``
    and a thread has no comments.
    """

    def __init__(self, tables=None):
        self.tables = tables or {}
        self.operations = []   # (kind, table_name, payload) in call order
        self._counters = {}

    def get_records_by_pks(self, base_id, table_name, pks, fields_str='*'):
        rows = self.tables.get(table_name, {})
        return {pk: dict(rows[pk]) for pk in pks if pk in rows}

    def get_thread_content(self, base_id, thread_ids, *, query, budget,
                           bodies_by_thread_id=None, extras_by_thread_id=None):
        bodies = bodies_by_thread_id or {}
        return {
            thread_id: SimpleNamespace(
                body=bodies.get(thread_id) or '',
                latest_message=None,
                history_messages=[],
                has_omitted_messages=False,
                extras=None,
            )
            for thread_id in thread_ids
        }

    def insert_rows(self, base_id, table_name, rows):
        start = self._counters.get(table_name, 0) + 1
        self._counters[table_name] = start + len(rows) - 1
        self.operations.append(('insert', table_name, rows))
        return {'pks': list(range(start, start + len(rows)))}

    def update_rows(self, base_id, table_name, updates):
        self.operations.append(('update', table_name, updates))
        return {}

    def writes(self, kind=None, table_name=None):
        return [
            payload for op, table, payload in self.operations
            if (kind is None or op == kind) and (table_name is None or table == table_name)
        ]


class ScriptedResponsesClient:
    """Return ResponsesResult values in order and record each request."""

    def __init__(self, scripts):
        self.scripts = list(scripts)
        self.calls = []

    def responses(self, **kwargs):
        self.calls.append({
            'input': kwargs.get('input'),
            'tools': kwargs.get('tools'),
            'tool_choice': kwargs.get('tool_choice'),
            'text': kwargs.get('text'),
        })
        if not self.scripts:
            raise AssertionError('ScriptedResponsesClient ran out of scripted responses')
        item = self.scripts.pop(0)
        if isinstance(item, ResponsesResult):
            return item
        return ResponsesResult(
            content=item.get('content', ''),
            tool_calls=item.get('tool_calls') or [],
            usage=item.get('usage') or {
                'input_tokens': 0,
                'output_tokens': 0,
                'total_tokens': 0,
            },
        )

    @property
    def system_prompts(self):
        """System prompt texts of the first request, for prompt-content checks."""
        if not self.calls:
            return []
        return [
            item['content']
            for item in self.calls[0]['input'] or []
            if isinstance(item, dict) and item.get('role') == 'system'
        ]
