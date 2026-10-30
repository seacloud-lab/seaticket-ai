from copy import deepcopy

import pytest

from tests.agent.support.builders import github_issue_record, make_case, search_hit
from tests.agent.support.fakes import FakeAIServices
from tests.agent.support.harness import validate_case, validate_expectations


def _case(**kwargs):
    return make_case(
        'github_issue_added', source_type='github_issue', connection_id=1, record_id=101,
        record=github_issue_record(101), **kwargs,
    )


def test_a_built_case_is_valid():
    validate_case(_case())


def test_an_unknown_key_in_the_input_is_rejected():
    case = _case()
    case['serch_results'] = {}

    with pytest.raises(ValueError, match='serch_results'):
        validate_case(case)


def test_a_missing_trigger_record_is_rejected():
    case = _case()
    case['records'] = []

    with pytest.raises(ValueError, match='records'):
        validate_case(case)


def test_the_trigger_must_be_among_the_records():
    case = _case()
    case['task']['record_id'] = 999

    with pytest.raises(ValueError, match='trigger record'):
        validate_case(case)


def test_a_search_hit_for_a_record_that_is_not_preloaded_is_rejected():
    case = _case(search_results={'issues_search': [
        search_hit('ticket', 555, title='Missing', content='c'),
    ]})

    with pytest.raises(ValueError, match='not in records'):
        validate_case(case)


def test_a_search_hit_for_a_preloaded_record_is_accepted():
    from tests.agent.support.builders import ticket_record

    case = _case(
        extra_records=[('ticket', None, ticket_record(555))],
        search_results={'issues_search': [
            search_hit('ticket', 555, title='Known', content='c'),
        ]},
    )

    validate_case(case)


def test_a_search_hit_missing_required_fields_is_rejected():
    case = _case()
    case['search_results'] = {'issues_search': [{'type': 'ticket', 'title': 'No content'}]}

    with pytest.raises(ValueError, match='content'):
        validate_case(case)


@pytest.mark.parametrize('expected', [
    {'suggestions': {'must_include': ['suggest_reply']}},  # unknown key
    {'suggestions': {'calls': [{'tool': 'suggest_reply', 'payloads': {}}]}},
    {'analysis': {'x': {'equal': 'Bug'}}},  # misspelt matcher
    {'runs': 0},
    {'search': {'max_calls': -1}},
])
def test_a_misspelt_or_unknown_rule_is_rejected(expected):
    with pytest.raises(ValueError):
        validate_expectations(expected)


def test_valid_expectations_are_accepted():
    validate_expectations({
        'analysis': {'classified_issue_type': {'equals': 'Bug'}},
        'suggestions': {
            'exactly_one_of': ['suggest_reply'],
            'forbidden': ['suggest_create_ticket'],
            'calls': [{'tool': 'suggest_reply', 'content': {'not_empty': True}}],
        },
        'search': {'first_tool': {'equals': 'issues_search'}, 'max_calls': 8},
        'runs': 3,
        'min_pass': 2,
    })


# ---- the fake search service must hand each tool its own results -------------

def _search(fake, tool_name, case):
    """Call the fake the way the real search tools do."""
    from seaqa_ai.utils.constants import ExtraSourceType

    context = {
        'issue_connections': case['project']['issue_connections'],
        'document_connections': case['project']['document_connections'],
    }
    if tool_name == 'issues_search':
        return fake.search(
            'q', context['issue_connections'],
            ExtraSourceType.get_issue_extra_source_types(), context, 5, lambda: None,
        )
    return fake.search(
        'q', context['document_connections'],
        ExtraSourceType.get_document_extra_source_types(), context, 5, lambda: None,
    )


def test_each_search_tool_gets_its_own_results_even_with_document_connections():
    case = _case(connections=[('site', 9)])
    fake = FakeAIServices({
        'issues_search': [{'title': 'an issue'}],
        'documents_search': [{'title': 'a document'}],
    })

    issues, _ = _search(fake, 'issues_search', case)
    documents, _ = _search(fake, 'documents_search', case)

    assert [row['title'] for row in issues] == ['an issue']
    assert [row['title'] for row in documents] == ['a document']
    assert [call['tool'] for call in fake.calls] == ['issues_search', 'documents_search']


def test_search_results_are_copies_so_a_case_is_not_mutated():
    case = _case()
    original = deepcopy({'issues_search': [{'title': 't'}]})
    fake = FakeAIServices(original)

    rows, _ = _search(fake, 'issues_search', case)
    rows[0]['title'] = 'changed'

    assert original == {'issues_search': [{'title': 't'}]}
