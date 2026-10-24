from seaqa_ai.agent.utils import compact_events


def test_events_of_different_types_stay_separate_and_keep_first_seen_order():
    events = [
        {'type': 'ticket_updated', 'old_value': {'state': 'open'}, 'new_value': {'state': 'closed'}},
        {'type': 'ticket_closed', 'old_value': {}, 'new_value': {}},
    ]

    assert [item['type'] for item in compact_events(events)] == ['ticket_updated', 'ticket_closed']


def test_repeated_updates_are_merged_keeping_the_first_old_and_the_last_new_value():
    events = [
        {'type': 'ticket_updated', 'old_value': {'priority': 1}, 'new_value': {'priority': 2}},
        {'type': 'ticket_updated', 'old_value': {'priority': 2}, 'new_value': {'priority': 3}},
    ]

    (merged,) = compact_events(events)

    assert merged == {
        'type': 'ticket_updated', 'old_value': {'priority': 1}, 'new_value': {'priority': 3},
    }


def test_comment_events_merge_per_comment_and_prefer_the_added_type():
    events = [
        {'type': 'ticket_comment_added', 'new_value': {'_pk': 5, 'content': 'a'}},
        {'type': 'ticket_comment_updated', 'new_value': {'_pk': 5, 'content': 'b'}},
        {'type': 'ticket_comment_added', 'new_value': {'_pk': 6, 'content': 'c'}},
    ]

    first, second = compact_events(events)

    assert first['type'] == 'ticket_comment_added'
    assert first['new_value']['content'] == 'b'
    assert second['new_value'] == {'_pk': 6, 'content': 'c'}


def test_non_dict_items_are_ignored_and_empty_values_become_none():
    (only,) = compact_events(['junk', {'type': 'ticket_closed'}])

    assert only == {'type': 'ticket_closed', 'old_value': None, 'new_value': None}
