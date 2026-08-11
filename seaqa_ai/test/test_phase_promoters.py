from types import SimpleNamespace

from seaqa_ai.agent.agent_service.phase_promoters import (
    AgentPhasePromoter,
    _RecordInputFormatter,
)


def make_promoter(*, event_type='github_issue_comment_added', context=None):
    task = SimpleNamespace(
        target_source_type='github_issue',
        target_connection_id=11,
        target_record_id=101,
        trigger_event=SimpleNamespace(event_type=event_type),
    )
    return AgentPhasePromoter(
        app=None,
        task=task,
        responses_client=None,
        react_tracer=None,
        context=context or {},
        record_cache={},
    )


def linked_ticket_context():
    return {
        'linked_ticket_identity': {
            'source_type': 'ticket',
            'connection_id': None,
            'record_id': 7,
        },
        'linked_ticket_record': {
            'linked_connection_records': [
                '12_202',
                '13_303',
                '12_202',
                'missing_separator',
                '99_404',
            ],
        },
        'issue_connections': [
            {'id': 11, 'type': 'github_issue'},
            {'id': 12, 'type': 'github_issue'},
            {'id': 13, 'type': 'email'},
        ],
    }


def test_prelude_injects_manually_built_linked_ticket_graph():
    promoter = make_promoter(context=linked_ticket_context())

    assert _RecordInputFormatter.build_prelude_records_input(
        promoter.task, promoter.event, promoter.context,
    ) == {
        'PreStep 1: Inject the record that triggered the event': [{
            'source_type': 'github_issue', 'connection_id': 11, 'record_id': 101,
        }],
        'PreStep 2: Inject the Ticket linked by the triggering record': [{
            'source_type': 'ticket', 'connection_id': None, 'record_id': 7,
        }],
        'PreStep 3: Inject other verified records linked to that Ticket': [
            {'source_type': 'github_issue', 'connection_id': 12, 'record_id': 202},
            {'source_type': 'email', 'connection_id': 13, 'record_id': 303},
        ],
    }


def test_prelude_uses_target_only_without_valid_linked_ticket_identity():
    context = linked_ticket_context()
    context['linked_ticket_identity'] = {
        'source_type': 'ticket', 'connection_id': 1, 'record_id': 7,
    }
    promoter = make_promoter(context=context)

    assert _RecordInputFormatter.build_prelude_records_input(
        promoter.task, promoter.event, promoter.context,
    ) == [{
        'source_type': 'github_issue', 'connection_id': 11, 'record_id': 101,
    }]


def test_prelude_uses_target_only_for_non_linked_ticket_events():
    promoter = make_promoter(
        event_type='github_issue_added', context=linked_ticket_context(),
    )

    assert _RecordInputFormatter.build_prelude_records_input(
        promoter.task, promoter.event, promoter.context,
    ) == [{
        'source_type': 'github_issue', 'connection_id': 11, 'record_id': 101,
    }]


def test_linked_ticket_siblings_exclude_the_triggering_record():
    context = linked_ticket_context()
    context['linked_ticket_record']['linked_connection_records'].insert(0, '11_101')
    promoter = make_promoter(context=context)

    siblings = _RecordInputFormatter._build_linked_ticket_sibling_records(
        promoter.task, promoter.context,
    )

    assert {'source_type': 'github_issue', 'connection_id': 11, 'record_id': 101} not in siblings


def test_event_records_input_reuses_linked_ticket_groups():
    promoter = make_promoter(context=linked_ticket_context())
    event_related_records = [
        {'source_type': 'github_issue', 'connection_id': 11, 'record_id': 101},
        {'source_type': 'ticket', 'connection_id': None, 'record_id': 7},
        {'source_type': 'email', 'connection_id': 13, 'record_id': 303},
    ]

    assert _RecordInputFormatter.build_event_records_input(
        promoter.task, promoter.context, event_related_records,
    ) == {
        'PreStep 1: Inject the record that triggered the event': [{
            'source_type': 'github_issue', 'connection_id': 11, 'record_id': 101,
        }],
        'PreStep 2: Inject the Ticket linked by the triggering record': [{
            'source_type': 'ticket', 'connection_id': None, 'record_id': 7,
        }],
        'PreStep 3: Inject other verified records linked to that Ticket': [{
            'source_type': 'email', 'connection_id': 13, 'record_id': 303,
        }],
    }
