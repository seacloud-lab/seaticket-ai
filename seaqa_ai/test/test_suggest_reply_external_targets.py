import pytest

from seaqa_ai.agent.tools.suggest_reply_external_targets import SuggestReplyExternalTargets


def _context(*linked_source_ids, trigger_source_id='9_99'):
    return {
        'trigger_source_id': trigger_source_id,
        'linked_ticket_record': {
            'linked_connection_records': list(linked_source_ids),
        },
    }


def _target(connection_id, record_id, source_type='github_issue', source_title='Issue'):
    return {
        'source_type': source_type,
        'connection_id': connection_id,
        'record_id': record_id,
        'source_title': source_title,
    }


def test_creates_reply_suggestion_for_linked_target():
    result = SuggestReplyExternalTargets().execute(
        reply='The work is ready for verification.',
        reason='The completed task supports a verification update.',
        reply_targets=[_target(2, 3, source_title='Linked issue')],
        context=_context('2_3'),
    )

    assert result == {
        'status': 'pending',
        'suggestion_text': 'Send a reply to the linked external record',
        'suggestion_content': 'The work is ready for verification.',
        'suggestion_reason': 'The completed task supports a verification update.',
        'trace_targets': [{
            'source_type': 'github_issue',
            'source_id': '2_3',
            'source_title': 'Linked issue',
        }],
        'trace_tool_name': 'suggest_reply',
    }


def test_filters_unlinked_triggering_and_duplicate_targets():
    result = SuggestReplyExternalTargets().execute(
        reply='An update is available.',
        reason='The completed task supports an update.',
        reply_targets=[
            _target(2, 3, source_title='First linked issue'),
            _target(2, 3, source_title='Duplicate linked issue'),
            _target(4, 5, source_title='Triggering issue'),
            _target(6, 7, source_title='Unlinked issue'),
            _target(8, 9, source_title='Second linked issue'),
        ],
        context=_context('2_3', '4_5', '8_9', trigger_source_id='4_5'),
    )

    assert result['trace_targets'] == [
        {
            'source_type': 'github_issue',
            'source_id': '2_3',
            'source_title': 'First linked issue',
        },
        {
            'source_type': 'github_issue',
            'source_id': '8_9',
            'source_title': 'Second linked issue',
        },
    ]


@pytest.mark.parametrize(
    ('reply', 'reason', 'reply_targets', 'error_message'),
    [
        ('', 'Supported reason', [_target(2, 3)], 'reply and reason must not be empty'),
        ('Supported reply', '', [_target(2, 3)], 'reply and reason must not be empty'),
        ('Supported reply', 'Supported reason', [], 'no external reply targets are available'),
        ('Supported reply', 'Supported reason', [_target(6, 7)], 'no external reply targets are available'),
        ('Supported reply', 'Supported reason', [_target(2, 3)], 'no external reply targets are available'),
    ],
)
def test_rejects_missing_or_ineligible_reply_targets(reply, reason, reply_targets, error_message):
    context = _context('2_3', trigger_source_id='2_3')

    with pytest.raises(ValueError, match=error_message):
        SuggestReplyExternalTargets().execute(
            reply=reply,
            reason=reason,
            reply_targets=reply_targets,
            context=context,
        )


def test_rejects_non_array_reply_targets():
    with pytest.raises(ValueError, match='reply_targets must be an array'):
        SuggestReplyExternalTargets().execute(
            reply='Supported reply',
            reason='Supported reason',
            reply_targets=_target(2, 3),
            context=_context('2_3'),
        )


@pytest.mark.parametrize(
    'target',
    [
        _target(None, 3),
        _target(2, 3, source_type='ticket'),
        _target(2, 3, source_type='unknown'),
    ],
)
def test_rejects_invalid_reply_target_identity(target):
    with pytest.raises(ValueError):
        SuggestReplyExternalTargets().execute(
            reply='Supported reply',
            reason='Supported reason',
            reply_targets=[target],
            context=_context('2_3'),
        )
