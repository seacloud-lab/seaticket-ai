import pytest
from jsonschema import Draft202012Validator

from seaqa_ai.agent.models.phase.prelude.spec import PreludePhaseSpec
from seaqa_ai.agent.utils.record_identity import SUPPORTED_RECORD_SOURCE_TYPES, validate_record_identity
from seaqa_ai.agent.tools.get_record import GetRecord
from seaqa_ai.agent.tools.suggest_close_ticket import SuggestCloseTicket
from seaqa_ai.agent.tools.suggest_notify_assignee import SuggestNotifyAssignee
from seaqa_ai.agent.tools.suggest_reply_external_targets import SuggestReplyExternalTargets


def _identity(source_type, connection_id, record_id=1):
    return {
        'source_type': source_type,
        'connection_id': connection_id,
        'record_id': record_id,
    }


@pytest.mark.parametrize(
    'schema',
    [
        GetRecord.tool['parameters'],
        PreludePhaseSpec.final_output_schema['properties']['event_related_records']['items'],
        SuggestCloseTicket.tool['parameters']['properties']['target'],
        SuggestNotifyAssignee.tool['parameters']['properties']['target'],
        SuggestReplyExternalTargets.tool['parameters']['properties']['reply_targets']['items'],
    ],
)
def test_identity_schemas_match_supported_get_record_source_types(schema):
    assert schema['properties']['source_type']['enum'] == list(SUPPORTED_RECORD_SOURCE_TYPES)
    assert 'Use null only for ticket and knowledge_base' in schema['properties']['connection_id']['description']


@pytest.mark.parametrize(
    'identity',
    [
        _identity('ticket', None),
        _identity('knowledge_base', None),
        _identity('github_issue', 1),
        _identity('discourse_forum', 1),
        _identity('email', 1),
        _identity('general_task', 1),
    ],
)
def test_record_identity_accepts_valid_source_connection_pairs(identity):
    assert validate_record_identity(**identity) == identity['source_type']


@pytest.mark.parametrize(
    'identity',
    [
        _identity('unknown', 1),
        _identity('ticket', 1),
        _identity('knowledge_base', 1),
        _identity('github_issue', None),
        _identity('discourse_forum', None),
        _identity('email', None),
        _identity('general_task', None),
    ],
)
def test_record_identity_rejects_invalid_source_connection_pairs(identity):
    with pytest.raises(ValueError):
        validate_record_identity(**identity)


@pytest.mark.parametrize(
    'identity',
    [
        _identity('ticket', 1),
        _identity('github_issue', None),
    ],
)
def test_identity_schema_rejects_invalid_source_connection_pairs(identity):
    schema = PreludePhaseSpec.final_output_schema['properties']['event_related_records']['items']

    assert list(Draft202012Validator(schema).iter_errors(identity))
