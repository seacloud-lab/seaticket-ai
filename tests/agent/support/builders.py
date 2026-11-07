"""Build agent test cases and scripted model outputs.

A case is a plain dict with the same shape as an ``input.yaml`` file:
``project``, ``task``, ``records`` and ``search_results``.
"""
import json
from copy import deepcopy

from seaqa_ai.agent.models.phase import AgentPhaseSpec
from seaqa_ai.agent.agent_service.phase_promoters import ALL_PHASES
from seaqa_ai.utils.constants import ConnectionType, ExtraSourceType


def identity(source_type, connection_id, record_id):
    return {
        'source_type': source_type,
        'connection_id': connection_id,
        'record_id': record_id,
    }


def issue_content(body, comments=()):
    """Content of a thread record: the opening body and its comments."""
    comments = list(comments)
    return {
        'content': body,
        'latest_comment': comments[-1] if comments else None,
        'history_comments': comments[:-1],
    }


def github_issue_record(
    record_id, *, title='Client crashes on start', body='The client aborts on start.',
    issue_number=None, state='open', labels=(), issue_type='',
    available_labels=('bug', 'enhancement', 'question'), linked_ticket=None, comments=(),
):
    return {
        '_pk': record_id,
        'issue_number': issue_number or record_id,
        'title': title,
        'state': state,
        'labels': list(labels),
        'issue_type': issue_type,
        'author': 'reporter',
        'url': f'https://example.test/issues/{issue_number or record_id}',
        'created_time': '2026-06-30T13:38:22Z',
        'linked_ticket': linked_ticket,
        'content': issue_content(body, comments),
        'available_labels': list(available_labels),
    }


def ticket_record(
    record_id, *, title='Ticket', body='', state='open', assignees=(), due_date=None,
    linked_connection_records=(), ticket_type='', priority=0,
):
    return {
        '_pk': record_id,
        'title': title,
        'state': state,
        'substate': '',
        'type': ticket_type,
        'tags': [],
        'assignees': list(assignees),
        'participants': [],
        'linked_connection_records': list(linked_connection_records),
        'priority': priority,
        'creator': 'owner',
        'comment_count': 0,
        'created_time': '2026-06-30T13:38:22Z',
        'modified_time': '2026-06-30T13:38:22Z',
        'closed_time': None,
        'due_date': due_date,
        'last_agent_processed_at': None,
        'content': {'latest_comment': None, 'content': body, 'history_comments': []},
    }


def search_hit(
    source_type, record_id, *, title, content, connection_id=None, url=None,
    modified_time='2026-06-01T00:00:00Z',
):
    """One pre-recorded search result, shaped like ``AIServices.search`` output."""
    if ExtraSourceType.is_valid(source_type):
        connection_id = None
    hit = {
        'type': source_type,
        'connection_id': connection_id,
        '_id': record_id,
        'title': title,
        'content': content,
        'modified_time': modified_time,
    }
    if url:
        hit['url'] = url
    return hit


def _connection_key(source_type):
    if source_type in ConnectionType.get_issue_connection_types():
        return 'issue_connections'
    if source_type in ConnectionType.get_task_connection_types():
        return 'task_connections'
    if source_type in ConnectionType.get_document_connection_types():
        return 'document_connections'
    return None


def make_case(
    event_type, *, source_type, connection_id, record_id, record, event=None,
    linked_ticket=None, extra_records=(), search_results=None, settings=None,
    connections=(), name='case',
):
    """Assemble a case. ``linked_ticket`` is a ticket record that owns the trigger.

    ``extra_records`` are ``(source_type, connection_id, record)`` tuples that
    are preloaded next to the trigger. ``connections`` are extra
    ``(source_type, id)`` project connections beyond the trigger's own.
    """
    record = deepcopy(record)
    records = [{'identity': identity(source_type, connection_id, record_id), 'record': record}]

    if linked_ticket is not None:
        ticket = deepcopy(linked_ticket)
        if connection_id is not None:
            source_id = f'{connection_id}_{record_id}'
            linked = list(ticket.get('linked_connection_records') or [])
            if source_id not in linked:
                linked.append(source_id)
            ticket['linked_connection_records'] = linked
        record['linked_ticket'] = ticket['_pk']
        records.append({
            'identity': identity('ticket', None, ticket['_pk']),
            'record': ticket,
        })
    for extra_type, extra_connection_id, extra_record in extra_records:
        records.append({
            'identity': identity(extra_type, extra_connection_id, extra_record['_pk']),
            'record': deepcopy(extra_record),
        })

    project = {
        'settings': deepcopy(settings) if settings is not None else {
            'lang': 'en', 'agent': {'model': None},
        },
        'issue_connections': [],
        'document_connections': [],
        'task_connections': [],
    }
    seen_connections = set()
    for item_type, item_id in [(source_type, connection_id), *connections]:
        key = _connection_key(item_type)
        if key is None or item_id is None or (item_type, item_id) in seen_connections:
            continue
        seen_connections.add((item_type, item_id))
        project[key].append({'id': item_id, 'type': item_type})

    if event is None:
        body = record.get('content')
        event = {
            'type': event_type,
            'old_value': {},
            'new_value': {
                'title': record.get('title', ''),
                'content': body.get('content', '') if isinstance(body, dict) else (body or ''),
            },
        }
    return {
        'name': name,
        'project': project,
        'task': {
            'source_type': source_type,
            'connection_id': connection_id,
            'record_id': record_id,
            'modified_time': None,
            'event': event,
        },
        'records': records,
        'search_results': deepcopy(search_results) if search_results else {},
    }


# ---- scripted model outputs -------------------------------------------------

def tool_call(name, call_id=None, **arguments):
    return {
        'type': 'function_call',
        'call_id': call_id or f'call_{name}',
        'name': name,
        'arguments': json.dumps(arguments),
    }


def tool_calls(*calls):
    return {'tool_calls': list(calls)}


def text(content):
    return {'content': content}


def prelude_output(*identities, summary='A new event was received.'):
    """Prelude final output. Each identity is ``(source_type, connection_id, record_id)``."""
    return text(json.dumps({
        'event_summary': summary,
        'event_related_records': [identity(*item) for item in identities],
    }))


_FIELD_DEFAULTS = {
    'string': '',
    'array': [],
    'integer': 0,
    'boolean': False,
    'object': {},
}


def _skill_field_default(schema):
    if 'enum' in schema:
        return schema['enum'][0]
    return deepcopy(_FIELD_DEFAULTS.get(schema.get('type'), ''))


def analysis_output(event_type, actions, *, skip_handling=None,
                    report='The report was analyzed.', **fields):
    """Analysis final output filled from the event skill's declared schema.

    Fields the skill adds (for example ``classified_issue_type``) get a valid
    default unless passed explicitly in ``fields``.
    """
    spec = ALL_PHASES['analysis']
    base_fields = spec.base_final_output_schema['properties']
    declared = AgentPhaseSpec.get_skill_metadata(event_type, 'analysis').get(
        'additional_output', {},
    )
    output = {
        'analysis_report': report,
        'recommended_actions': list(actions),
        'skip_handling': (
            list(actions) == ['no_op'] if skip_handling is None else skip_handling
        ),
    }
    for name, schema in declared.items():
        if name not in base_fields:
            output[name] = _skill_field_default(schema)
    output.update(fields)
    return text(json.dumps(output))
