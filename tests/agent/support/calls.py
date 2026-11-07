"""Helpers for reading the requests a ScriptedResponsesClient received."""
import json


def system_messages(call):
    return [
        item['content'] for item in call['input'] or []
        if isinstance(item, dict) and item.get('role') == 'system'
    ]


def user_payload(call):
    """The JSON object of the user message of one request."""
    for item in call['input'] or []:
        if isinstance(item, dict) and item.get('role') == 'user':
            return json.loads(item['content'])
    raise AssertionError('request has no user message')


def get_record_calls(call):
    """Identities of the get_record calls injected into one request, in order."""
    identities = []
    for item in call['input'] or []:
        if isinstance(item, dict) and item.get('type') == 'function_call' \
                and item.get('name') == 'get_record':
            identities.append(json.loads(item['arguments']))
    return identities


def ordered_records_prompts(call):
    """System messages that introduce injected records, in order."""
    return [text for text in system_messages(call) if text.startswith('PreStep')]


def error_rows(rows, phase=None):
    return [
        row for row in rows
        if row['action_type'] == 'error' and (phase is None or row['phase'] == phase)
    ]
