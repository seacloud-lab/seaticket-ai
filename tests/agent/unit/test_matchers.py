import json
from pathlib import Path

import pytest

from tests.agent.support.matchers import (
    MATCHERS, evaluate, validate_matchers, validate_matcher,
)
from tests.agent.support.harness import AgentRunResult, SCHEMA_PATH


def _run(analysis=None, suggestions=(), search_calls=()):
    return AgentRunResult(
        phases={'analysis': analysis or {}},
        suggestions=list(suggestions),
        tracer_rows=[],
        search_calls=list(search_calls),
        model_id=None,
    )


def _suggestion(tool, payload=None, content='', reason='r', target=('github_issue', '1_1')):
    return {
        'tool_name': tool, 'suggestion_payload': payload or {},
        'suggestion_content': content, 'suggestion_reason': reason,
        'target_item_type': target[0], 'target_item_id': target[1],
    }


@pytest.mark.parametrize('name, arg, value, expected', [
    ('equals', 'Bug', 'Bug', True),
    ('equals', 'Bug', 'bug', False),
    ('one_of', ['a', 'b'], 'b', True),
    ('includes_all', ['A', 'b'], ['a', 'B', 'c'], True),
    ('includes_all', ['a', 'z'], ['a'], False),
    ('includes_any', ['Type-X'], ['type-x'], True),
    ('includes_any', ['x'], None, False),
    ('subset_of', ['bug', 'docs'], ['BUG'], True),
    ('subset_of', ['bug'], ['bug', 'other'], False),
    ('length_between', [1, 3], ['a', 'b'], True),
    ('length_between', [1, 3], [], False),
    ('length_between', [1, 3], None, False),
    ('not_empty', True, ' x ', True),
    ('not_empty', True, '  ', False),
    ('not_empty', False, '', True),
    ('contains_any', ['HTTPS://docs'], 'see https://docs.example', True),
    ('contains_any', ['x'], None, False),
    ('not_contains', ['ticket #'], 'Ticket #12 is open', False),
    ('not_contains', ['ticket #'], 'All good', True),
    ('matches_regex', r'^\d+$', '123', True),
    ('matches_regex', r'^\d+$', 'a1', False),
])
def test_matchers(name, arg, value, expected):
    function, _kind = MATCHERS[name]

    assert function(value, arg) is expected


def test_schema_and_registry_list_the_same_matchers():
    schema = json.loads(Path(SCHEMA_PATH).read_text(encoding='utf-8'))

    assert set(schema['$defs']['matcher']['propertyNames']['enum']) == set(MATCHERS)


@pytest.mark.parametrize('matcher, message', [
    ({'nope': 1}, 'unknown matcher'),
    ({}, 'at least one matcher'),
    ({'equals': 1, 'one_of': 5}, 'needs a list argument'),
    ({'includes_any': 'x'}, 'needs a list argument'),
    ({'length_between': [1]}, 'needs a pair argument'),
    ({'not_empty': 'yes'}, 'needs a bool argument'),
    ('equals', 'at least one matcher'),
])
def test_a_malformed_matcher_is_rejected(matcher, message):
    with pytest.raises(ValueError, match=message):
        validate_matcher(matcher, 'where')


def test_validate_matchers_names_the_rule_that_is_wrong():
    expected = {'suggestions': {'calls': [
        {'tool': 'suggest_reply', 'payload': {'x': {'includes_any': 'oops'}}},
    ]}}

    with pytest.raises(ValueError, match=r'calls\[0\]\(suggest_reply\)\.payload\.x'):
        validate_matchers(expected)


def test_a_value_must_satisfy_every_matcher_of_a_rule():
    expected = {'suggestions': {'calls': [{'tool': 'suggest_assign_labels', 'payload': {
        'suggested_labels': {'includes_any': ['bug'], 'length_between': [1, 1]},
    }}]}}
    run = _run(suggestions=[
        _suggestion('suggest_assign_labels', {'suggested_labels': ['bug', 'docs']}),
    ])

    (failure,) = evaluate(run, expected, {})

    assert 'length_between' in failure


def test_a_passing_run_has_no_failures():
    expected = {
        'analysis': {'classified_issue_type': {'equals': 'Bug'}},
        'suggestions': {
            'exactly_one_of': ['suggest_create_ticket', 'suggest_reply'],
            'forbidden': ['suggest_reply'],
            'calls': [
                {'tool': 'suggest_assign_labels', 'payload': {
                    'suggested_labels': {'includes_any': ['bug'], 'subset_of': '$available_labels'},
                }},
                {'tool': 'suggest_create_ticket', 'reason': {'not_empty': True},
                 'target': {'type': {'equals': 'github_issue'}}},
            ],
        },
        'search': {'first_tool': {'equals': 'issues_search'}, 'max_calls': 2},
    }
    run = _run(
        analysis={'classified_issue_type': 'Bug'},
        suggestions=[
            _suggestion('suggest_assign_labels', {'suggested_labels': ['bug']}),
            _suggestion('suggest_create_ticket'),
        ],
        search_calls=[{'tool': 'issues_search', 'query': 'q'}],
    )

    assert evaluate(run, expected, {'available_labels': ['bug', 'docs']}) == []


def test_every_broken_rule_is_reported_with_where_it_failed():
    expected = {
        'analysis': {'classified_issue_type': {'equals': 'Bug'}},
        'suggestions': {
            'exactly_one_of': ['suggest_create_ticket', 'suggest_reply'],
            'forbidden': ['suggest_reply'],
            'calls': [
                {'tool': 'suggest_assign_labels', 'payload': {
                    'suggested_labels': {'subset_of': '$available_labels'},
                }},
                {'tool': 'suggest_modify_type'},
            ],
        },
        'search': {'first_tool': {'equals': 'issues_search'}, 'max_calls': 1},
    }
    run = _run(
        analysis={'classified_issue_type': 'Feature'},
        suggestions=[
            _suggestion('suggest_reply'),
            _suggestion('suggest_create_ticket'),
            _suggestion('suggest_assign_labels', {'suggested_labels': ['made-up']}),
        ],
        search_calls=[{'tool': 'documents_search', 'query': 'a'},
                      {'tool': 'issues_search', 'query': 'b'}],
    )

    failures = evaluate(run, expected, {'available_labels': ['bug']})

    joined = '\n'.join(failures)
    assert 'analysis.classified_issue_type' in joined
    assert 'expected exactly one of' in joined
    assert 'suggest_reply must not be called' in joined
    assert 'suggestions.suggest_assign_labels.payload.suggested_labels' in joined
    assert 'suggest_modify_type was not called' in joined
    assert 'search.first_tool' in joined
    assert 'at most 1 allowed' in joined


def test_an_unknown_variable_is_a_failure_not_a_silent_pass():
    expected = {'suggestions': {'calls': [{'tool': 'suggest_assign_labels', 'payload': {
        'suggested_labels': {'subset_of': '$no_such_variable'},
    }}]}}
    run = _run(suggestions=[_suggestion('suggest_assign_labels', {'suggested_labels': ['x']})])

    (failure,) = evaluate(run, expected, {})

    assert 'unknown variable $no_such_variable' in failure


def test_variables_can_read_nested_fields():
    expected = {'suggestions': {'calls': [{'tool': 'suggest_reply', 'content': {
        'contains_any': ['$trigger_record.url'],
    }}]}}
    run = _run(suggestions=[_suggestion('suggest_reply', content='see https://x.test/1')])

    assert evaluate(run, expected, {'trigger_record': {'url': 'https://x.test/1'}}) == []
