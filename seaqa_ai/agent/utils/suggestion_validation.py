"""Validation shared by suggestion creation and draft editing."""

import json
from copy import deepcopy


ISSUE_TYPES = ('Bug', 'Feature', 'Question')
TICKET_FIELDS = ('title', 'content', 'type', 'priority', 'tags')
TEXT_CONTENT_MODE = 'text'
TICKET_CONTENT_MODE = 'ticket_json'


def suggestion_tool_class(tool_name):
    from seaqa_ai.agent.utils import get_tools_map

    tool_class = get_tools_map().get(tool_name)
    if tool_class is None or not hasattr(tool_class, 'editable_content_mode'):
        return None
    return tool_class


def persisted_tool_names(allowed_tools):
    from seaqa_ai.agent.utils import get_tools_map

    tools_map = get_tools_map()
    names = set()
    for tool_name in allowed_tools or []:
        if tool_name in ('modify_suggestion', 'remove_suggestion'):
            continue
        tool_class = tools_map.get(tool_name)
        names.add(getattr(tool_class, 'persisted_tool_name', None) or tool_name)
    return names


def validate_labels(labels, available):
    if not isinstance(labels, list) or not labels or not all(isinstance(label, str) for label in labels):
        raise ValueError('suggested_labels must be a non-empty array of label names')
    stripped = [label.strip() for label in labels]
    if not all(stripped):
        raise ValueError('suggested_labels must be a non-empty array of label names')
    if not isinstance(available, list) or not available:
        raise ValueError('available_labels must be available to suggest labels')
    available_normalized = {str(label).casefold() for label in available}
    if any(label.casefold() not in available_normalized for label in stripped):
        raise ValueError('suggested_labels must be selected from available_labels')
    return stripped


def validate_issue_type(value):
    if value not in ISSUE_TYPES:
        raise ValueError('suggested_type must be Bug, Feature, or Question')


def validate_related_ticket(value):
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ValueError('related_ticket must be a positive integer')


def is_json_object(content):
    if not isinstance(content, str):
        return False
    try:
        parsed = json.loads(content)
    except ValueError:
        return False
    return isinstance(parsed, dict)


def validate_suggestion_content(tool_name, content):
    if not isinstance(content, str) or not content.strip():
        raise ValueError('suggestion_content must not be empty')
    if tool_name != 'suggest_create_ticket':
        return
    try:
        ticket = json.loads(content)
    except ValueError:
        # Older ticket suggestions may contain a plain-text description.
        if content.lstrip().startswith(('{', '[')):
            raise ValueError('Ticket suggestion content must be valid JSON') from None
        return
    if not isinstance(ticket, dict):
        raise ValueError('Ticket suggestion content must be an object')
    _validate_ticket_object(ticket)


def _is_positive_int(value):
    return not isinstance(value, bool) and isinstance(value, int) and value > 0


def _validate_ticket_object(ticket):
    if not isinstance(ticket.get('title'), str) or not ticket['title'].strip():
        raise ValueError('Ticket suggestion title must not be empty')
    if not isinstance(ticket.get('content'), str):
        raise ValueError('Ticket suggestion content must be a string')
    if 'type' in ticket and not isinstance(ticket['type'], str):
        raise ValueError('Ticket suggestion type must be a string')
    if 'priority' in ticket and (
        isinstance(ticket['priority'], bool)
        or not isinstance(ticket['priority'], int) or ticket['priority'] < 0
    ):
        raise ValueError('Ticket suggestion priority must be a non-negative integer')
    if 'tags' in ticket and (
        not isinstance(ticket['tags'], list)
        or not all(_is_positive_int(tag) for tag in ticket['tags'])
    ):
        raise ValueError('Ticket suggestion tags must be an array of positive integers')


def _ticket_object_from_content(content):
    if isinstance(content, str) and content.strip():
        try:
            parsed = json.loads(content)
        except ValueError:
            parsed = None
        if isinstance(parsed, dict):
            return deepcopy(parsed)
        if parsed is not None:
            raise ValueError('Ticket suggestion content must be an object')
        return {
            'title': '',
            'content': content,
            'type': '',
            'priority': 0,
            'tags': [],
        }
    return {
        'title': '',
        'content': '',
        'type': '',
        'priority': 0,
        'tags': [],
    }


def _resolve_ticket_type(value, field_context):
    if not isinstance(value, str):
        raise ValueError('Ticket suggestion type is invalid')
    stripped = value.strip()
    if not stripped:
        return ''
    options = (field_context or {}).get('type_options')
    if options is None:
        raise ValueError('Ticket type options are unavailable')
    lowered = stripped.casefold()
    for option in options:
        option_id = str(option.get('id') or '').strip()
        option_name = str(option.get('name') or '').strip()
        if option_id and option_name and lowered == option_name.casefold():
            return option_id
    raise ValueError('Ticket suggestion type must match an available ticket type')


def _resolve_ticket_tags(value, field_context):
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise ValueError('Ticket suggestion tags must be an array of tag names')
    if not value:
        return []
    tags = (field_context or {}).get('tags')
    if not tags:
        raise ValueError('No project tags are available to set on ticket suggestions')

    ids_by_name = {}
    for tag in tags:
        tag_id = tag.get('id')
        if not _is_positive_int(tag_id):
            continue
        name_key = str(tag.get('name') or '').strip().casefold()
        if name_key and name_key not in ids_by_name:
            ids_by_name[name_key] = tag_id

    resolved = []
    seen = set()
    for item in value:
        raw = item.strip()
        if not raw:
            raise ValueError('Ticket suggestion tags must not contain empty names')
        tag_id = ids_by_name.get(raw.casefold())
        if tag_id is None:
            raise ValueError('Ticket suggestion tags must be selected from available project tags')
        if tag_id not in seen:
            seen.add(tag_id)
            resolved.append(tag_id)
    return resolved


def _normalize_ticket_field(field, value, field_context=None):
    if field in ('title', 'content'):
        if not isinstance(value, str) or (field == 'title' and not value.strip()):
            raise ValueError(f'Ticket suggestion {field} is invalid')
        return value
    if field == 'type':
        return _resolve_ticket_type(value, field_context)
    if field == 'priority':
        if isinstance(value, bool) or not isinstance(value, int) or value < 0 or value > 4:
            raise ValueError('Ticket suggestion priority must be an integer between 0 and 4')
        return value
    if field == 'tags':
        return _resolve_ticket_tags(value, field_context)
    raise ValueError('ticket_fields contains unsupported fields')


def apply_ticket_fields(content, ticket_fields, field_context=None):
    if not isinstance(ticket_fields, dict) or not ticket_fields:
        raise ValueError('ticket_fields must be a non-empty object')
    if set(ticket_fields) - set(TICKET_FIELDS):
        raise ValueError('ticket_fields contains unsupported fields')
    ticket = _ticket_object_from_content(content)
    for field, value in ticket_fields.items():
        ticket[field] = _normalize_ticket_field(field, value, field_context)
    _validate_ticket_object(ticket)
    return json.dumps(ticket, ensure_ascii=False)


def _validate_payload_values(payload, context):
    """Validate and normalize supported payload field values in place."""
    if 'suggested_labels' in payload:
        payload['suggested_labels'] = validate_labels(
            payload['suggested_labels'],
            (context.get('trigger_record') or {}).get('available_labels'),
        )
    if 'suggested_type' in payload:
        validate_issue_type(payload['suggested_type'])
    if 'current_issue_type' in payload and not isinstance(payload['current_issue_type'], str):
        raise ValueError('current_issue_type must be a string')
    if 'related_ticket' in payload:
        from seaqa_ai.agent.tools._basic_agent_suggestion_tool import BasicAgentSuggestionTool

        validate_related_ticket(payload['related_ticket'])
        BasicAgentSuggestionTool._loaded_target(
            {'source_type': 'ticket', 'source_id': str(payload['related_ticket'])},
            context, 'ticket',
        )


def validate_client_draft(draft, context, *, original=None):
    """Validate a complete draft carried in from an earlier regeneration turn.

    - When ``original`` is None, ``draft`` is a ``draft:<uuid>`` added in an earlier turn.
    - When ``original`` is present, ``draft`` is a modified ``action:<pk>`` row and only
      fields that differ from the DB baseline are revalidated against live context.
    """
    tool_name = draft['tool_name']
    tool_class = suggestion_tool_class(tool_name)
    content_mode = getattr(tool_class, 'editable_content_mode', None)
    editable_fields = set(getattr(tool_class, 'editable_payload_fields', ()) or ())
    readonly_fields = set(getattr(tool_class, 'readonly_payload_fields', ()) or ())

    if original is None:
        if not draft['suggestion_reason'].strip():
            raise ValueError('suggestion_reason must not be empty')
        if content_mode is not None:
            validate_suggestion_content(tool_name, draft['suggestion_content'])
        elif draft['suggestion_content']:
            raise ValueError('This suggestion does not have editable content')
        payload = draft['suggestion_payload']
        if set(payload) - (editable_fields | readonly_fields):
            raise ValueError('This suggestion does not allow those payload fields to be modified')
        if editable_fields - set(payload):
            raise ValueError('Suggestion is missing required payload fields')
        _validate_payload_values(payload, context)
        return

    if draft['suggestion_reason'] != original['suggestion_reason'] and not draft['suggestion_reason'].strip():
        raise ValueError('suggestion_reason must not be empty')

    if draft['suggestion_content'] != original['suggestion_content']:
        if content_mode is None:
            raise ValueError('This suggestion does not have editable content')
        validate_suggestion_content(tool_name, draft['suggestion_content'])
        if (
            content_mode == TICKET_CONTENT_MODE
            and is_json_object(original['suggestion_content'])
            and not is_json_object(draft['suggestion_content'])
        ):
            raise ValueError('Ticket suggestion content must remain valid JSON')

    before, after = original['suggestion_payload'], draft['suggestion_payload']
    if set(before) - set(after):
        raise ValueError('Existing payload fields cannot be removed')
    changed_payload = {
        key: value for key, value in after.items()
        if key not in before or value != before[key]
    }
    if set(changed_payload) - editable_fields:
        raise ValueError('This suggestion does not allow those payload fields to be modified')
    if changed_payload:
        _validate_payload_values(changed_payload, context)
        after.update(changed_payload)


def apply_suggestion_changes(draft, changes, context):
    """Validate a ``modify_suggestion`` patch and return an updated copy."""
    if not isinstance(changes, dict) or not changes:
        raise ValueError('changes must be a non-empty object')
    tool_name = draft['tool_name']
    tool_class = suggestion_tool_class(tool_name)
    content_mode = getattr(tool_class, 'editable_content_mode', None)
    allowed = {'suggestion_reason', 'suggestion_payload'}
    if content_mode == TEXT_CONTENT_MODE:
        allowed.add('suggestion_content')
    elif content_mode == TICKET_CONTENT_MODE:
        allowed.add('ticket_fields')
    if set(changes) - allowed:
        if content_mode == TICKET_CONTENT_MODE and 'suggestion_content' in changes:
            raise ValueError('Ticket suggestions must be edited with ticket_fields')
        raise ValueError('Only content, reason, and supported payload fields may be modified')

    candidate = deepcopy(draft)
    if 'suggestion_reason' in changes:
        reason = changes['suggestion_reason']
        if not isinstance(reason, str) or not reason.strip():
            raise ValueError('suggestion_reason must not be empty')
        candidate['suggestion_reason'] = reason
    if 'ticket_fields' in changes:
        candidate['suggestion_content'] = apply_ticket_fields(
            candidate['suggestion_content'],
            changes['ticket_fields'],
            (context or {}).get('ticket_field_context'),
        )
    if 'suggestion_content' in changes:
        validate_suggestion_content(tool_name, changes['suggestion_content'])
        candidate['suggestion_content'] = changes['suggestion_content']
    if 'suggestion_payload' in changes:
        payload_changes = changes['suggestion_payload']
        if not isinstance(payload_changes, dict) or not payload_changes:
            raise ValueError('suggestion_payload must be a non-empty object')
        editable_fields = set(getattr(tool_class, 'editable_payload_fields', ()) or ())
        if set(payload_changes) - editable_fields:
            raise ValueError('This suggestion does not allow those payload fields to be modified')
        patched_payload = deepcopy(payload_changes)
        _validate_payload_values(patched_payload, context)
        candidate['suggestion_payload'].update(patched_payload)
    return candidate
