import pytest

from seaqa_ai.agent.utils.loaded_records import LoadedRecords
from seaqa_ai.agent.utils.record_identity import (
    identity_key, record_identity_schema, validate_record_identity,
)


@pytest.mark.parametrize('source_type, connection_id, record_id', [
    ('ticket', None, 1),
    ('portal_issue', None, 1),
    ('github_issue', 2, 1),
    ('email', 0, 1),
])
def test_valid_identities_are_accepted(source_type, connection_id, record_id):
    assert validate_record_identity(source_type, connection_id, record_id) == source_type


@pytest.mark.parametrize('source_type, connection_id, record_id, message', [
    ('slack', 1, 1, 'Unsupported record source type'),
    ('ticket', None, '1', 'record_id must be an integer'),
    ('ticket', None, True, 'record_id must be an integer'),
    ('ticket', 5, 1, 'connection_id must be null'),
    ('github_issue', None, 1, 'connection_id must be an integer'),
    ('github_issue', True, 1, 'connection_id must be an integer'),
])
def test_invalid_identities_are_rejected(source_type, connection_id, record_id, message):
    with pytest.raises(ValueError, match=message):
        validate_record_identity(source_type, connection_id, record_id)


def test_identity_key_is_none_for_malformed_identities():
    assert identity_key({'source_type': 'ticket', 'connection_id': None, 'record_id': 1}) == (
        'ticket', None, 1,
    )
    assert identity_key({'source_type': 'ticket', 'connection_id': '1', 'record_id': 1}) is None
    assert identity_key({'source_type': '', 'connection_id': None, 'record_id': 1}) is None
    assert identity_key('ticket') is None


def test_nested_schema_separates_connection_less_and_connection_sources():
    branches = record_identity_schema(nested=True)['anyOf']

    without, with_connection = branches
    assert 'ticket' in without['properties']['source_type']['enum']
    assert without['properties']['connection_id']['type'] == 'null'
    assert 'github_issue' in with_connection['properties']['source_type']['enum']
    assert with_connection['properties']['connection_id']['type'] == 'integer'


def test_loaded_records_resolve_trace_style_source_ids():
    records = LoadedRecords()
    records.add('ticket', None, 7, {'title': 'ticket'})
    records.add('github_issue', 2, 50, {'title': 'issue'})

    assert records.find_by_source_id('ticket', '7') == {'title': 'ticket'}
    assert records.find_by_source_id('github_issue', '2_50') == {'title': 'issue'}
    assert records.find_by_source_id('github_issue', '50') is None
    assert records.contains({'source_type': 'ticket', 'connection_id': None, 'record_id': 7})
    assert not records.contains({'source_type': 'ticket', 'connection_id': None, 'record_id': 8})
