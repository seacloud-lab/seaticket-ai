import pytest

from seaqa_ai.agent.tools.suggest_assign_labels import SuggestAssignLabels
from seaqa_ai.agent.tools.suggest_modify_type import SuggestModifyType
from seaqa_ai.agent.tools.suggest_reply import SuggestReply


def _context(available_labels):
    return {
        'trigger_source_type': 'github_issue',
        'trigger_source_id': '1_101',
        'trigger_source_title': 'Sync client crashes',
        'trigger_record': {'available_labels': available_labels},
    }


def test_assign_labels_rejects_empty_labels():
    with pytest.raises(ValueError, match='must not be empty'):
        SuggestAssignLabels().execute([], 'The issue is a crash.', context=_context(['bug']))


def test_assign_labels_rejects_empty_reason():
    with pytest.raises(ValueError, match='must not be empty'):
        SuggestAssignLabels().execute(['bug'], '   ', context=_context(['bug']))


def test_assign_labels_rejects_unknown_label():
    with pytest.raises(ValueError, match='available_labels'):
        SuggestAssignLabels().execute(
            ['not-a-real-label'],
            'This label describes the crash.',
            context=_context(['bug', 'type-possiblyabug']),
        )


def test_assign_labels_matches_available_labels_case_insensitively():
    result = SuggestAssignLabels().execute(
        ['type-possiblyabug'],
        'The report describes a crash.',
        context=_context(['Type-PossiblyABug', 'status-indev']),
    )
    assert result['suggestion_payload'] == {'suggested_labels': ['type-possiblyabug']}


@pytest.mark.parametrize('reply, reason', [('', 'a reason'), ('  ', 'a reason'),
                                           ('A reply.', ''), ('A reply.', '   ')])
def test_reply_needs_both_a_reply_and_a_reason(reply, reason):
    with pytest.raises(ValueError, match='must not be empty'):
        SuggestReply().execute(reply, reason, context=_context(['bug']))


def test_reply_keeps_the_reply_as_content_and_the_trimmed_reason():
    result = SuggestReply().execute(' Thanks. ', ' Needs an answer. ', context=_context(['bug']))

    assert result['suggestion_content'] == 'Thanks.'
    assert result['suggestion_reason'] == 'Needs an answer.'
    assert result['status'] == 'pending'


def test_modify_type_payload_keeps_current_and_suggested_type():
    result = SuggestModifyType().execute(
        '',
        'Bug',
        'The client crashes when the menu bar icon is clicked.',
        context=_context(['bug']),
    )
    assert result['suggestion_payload'] == {
        'current_issue_type': '',
        'suggested_type': 'Bug',
    }
    assert result['status'] == 'pending'
