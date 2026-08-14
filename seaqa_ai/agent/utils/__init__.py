import importlib
import inspect
from copy import deepcopy
from collections.abc import Iterable
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml

from seaqa_ai.utils.tools import BasicTool
from seaqa_ai.utils.tools.search_tools import DocumentsSearch, IssuesSearch



TOOLS_PACKAGE = 'seaqa_ai.agent.tools'
TOOLS_DIR = Path(__file__).resolve().parent.parent / 'tools'
SKILLS_DIR = Path(__file__).resolve().parent.parent / 'skills'


def to_strict_response_schema(schema: dict) -> dict:
    """Convert a JSON Schema to the strict subset required by Responses output."""
    if not isinstance(schema, dict):
        raise TypeError('schema must be an object')
    schema = deepcopy(schema)
    strict = _schema_has_only_required_properties(schema)
    schema = _to_strict_response_schema(schema)
    _set_additional_properties(schema, strict)
    return {
        'schema': schema,
        'strict': strict,
    }


def _to_strict_response_schema(schema: dict) -> dict:
    for key in ('allOf', 'anyOf', 'oneOf'):
        if isinstance(schema.get(key), list):
            schema[key] = [
                _to_strict_response_schema(item) if isinstance(item, dict) else item
                for item in schema[key]
            ]

    if isinstance(schema.get('items'), dict):
        schema['items'] = _to_strict_response_schema(schema['items'])

    properties = schema.get('properties')
    if not isinstance(properties, dict):
        return schema

    required = schema.get('required') or []
    if not isinstance(required, list) or not all(isinstance(name, str) for name in required):
        raise ValueError('schema required fields must be a string array')
    for name, property_schema in properties.items():
        if not isinstance(property_schema, dict):
            raise ValueError(f'schema property {name!r} must be an object')
        properties[name] = _to_strict_response_schema(property_schema)
    return schema


def _schema_has_only_required_properties(schema: dict) -> bool:
    for key in ('allOf', 'anyOf', 'oneOf'):
        if any(
            isinstance(item, dict) and not _schema_has_only_required_properties(item)
            for item in schema.get(key, [])
        ):
            return False
    if isinstance(schema.get('items'), dict) and not _schema_has_only_required_properties(schema['items']):
        return False
    properties = schema.get('properties')
    if not isinstance(properties, dict):
        return True
    required = schema.get('required') or []
    if not isinstance(required, list) or not all(isinstance(name, str) for name in required):
        raise ValueError('schema required fields must be a string array')
    if not set(properties).issubset(required):
        return False
    return all(_schema_has_only_required_properties(property_schema)
               for property_schema in properties.values())


def _set_additional_properties(schema: dict, strict: bool):
    for key in ('allOf', 'anyOf', 'oneOf'):
        for item in schema.get(key, []):
            if isinstance(item, dict):
                _set_additional_properties(item, strict)
    if isinstance(schema.get('items'), dict):
        _set_additional_properties(schema['items'], strict)
    properties = schema.get('properties')
    if not isinstance(properties, dict):
        return
    for property_schema in properties.values():
        if isinstance(property_schema, dict):
            _set_additional_properties(property_schema, strict)
    if strict:
        schema['additionalProperties'] = False
    else:
        schema.pop('additionalProperties', None)

def _tool_importer(tool_name):
    module = importlib.import_module(f'{TOOLS_PACKAGE}.{tool_name}')
    tool_classes = [
        tool_class
        for _, tool_class in inspect.getmembers(module, inspect.isclass)
        if (
            tool_class is not BasicTool
            and tool_class.__module__ == module.__name__
            and issubclass(tool_class, BasicTool)
        )
    ]
    if len(tool_classes) != 1:
        raise ValueError(
            f'Tool module {tool_name!r} must define exactly one BasicTool subclass'
        )
    return tool_classes[0]


@lru_cache
def get_tools_map():
    tools = {
        tool_file.stem: _tool_importer(tool_file.stem)
        for tool_file in TOOLS_DIR.iterdir()
        if (
            tool_file.is_file()
            and tool_file.suffix == '.py'
            and not tool_file.name.startswith('_')
        )
    }
    tools.update({
        'documents_search': DocumentsSearch,
        'issues_search': IssuesSearch
    })
    return tools

@lru_cache
def get_skill_map() -> dict[str, dict[str, str]]:
    skills_map = {}

    for event_dir in SKILLS_DIR.iterdir():
        if event_dir.name.startswith('.') or not event_dir.is_dir():
            continue

        phases = {}
        for skill_file in event_dir.iterdir():
            if (
                skill_file.name.startswith('.')
                or not skill_file.is_file()
                or skill_file.suffix != '.md'
            ):
                continue
            skill = skill_file.read_text(encoding='utf-8')
            _validate_skill(skill, event_dir.name, skill_file.stem)
            phases[skill_file.stem] = skill

        if phases:
            skills_map[event_dir.name] = phases

    return skills_map


def _validate_skill(skill, event, phase):
    metadata_text, separator, _ = skill.partition('====================')
    if not separator:
        raise ValueError(f'Skill {event}/{phase} must contain the required metadata separator')
    try:
        metadata = yaml.safe_load(metadata_text)
    except yaml.YAMLError as error:
        raise ValueError(f'Skill {event}/{phase} metadata must be valid YAML') from error
    if not isinstance(metadata, dict):
        raise ValueError(f'Skill {event}/{phase} metadata must be an object')
    if metadata.get('event') != event or metadata.get('phase') != phase:
        raise ValueError(f'Skill metadata must match its path: {event}/{phase}')
    if not isinstance(metadata.get('description'), str) or not metadata['description'].strip():
        raise ValueError(f'Skill {event}/{phase} requires a description')
    tool_names = metadata.get('tools')
    if not isinstance(tool_names, list) or not all(isinstance(name, str) for name in tool_names):
        raise ValueError(f'Skill {event}/{phase} tools must be a string array')
    if phase != 'prelude' and 'get_record' in tool_names:
        raise ValueError(f'Only prelude skills may use get_record: {event}/{phase}')
    _validate_additional_output(metadata.get('additional_output'), event, phase)


def _validate_additional_output(additional_output, event, phase):
    if additional_output is None:
        return
    if not isinstance(additional_output, dict):
        raise ValueError(f'Skill {event}/{phase} additional_output must be an object')
    if not additional_output:
        raise ValueError(f'Skill {event}/{phase} additional_output must not be empty')
    for name, property_schema in additional_output.items():
        if not isinstance(name, str) or not name:
            raise ValueError(f'Skill {event}/{phase} additional_output property names must be strings')
        if not isinstance(property_schema, dict):
            raise ValueError(
                f'Skill {event}/{phase} additional_output property {name!r} must be an object'
            )
        if not isinstance(property_schema.get('type'), str):
            raise ValueError(
                f'Skill {event}/{phase} additional_output property {name!r} requires a type'
            )
        if not isinstance(property_schema.get('description'), str) or not property_schema['description'].strip():
            raise ValueError(
                f'Skill {event}/{phase} additional_output property {name!r} requires a description'
            )
        required = property_schema.get('required', True)
        if not isinstance(required, bool):
            raise ValueError(
                f'Skill {event}/{phase} additional_output property {name!r} required must be boolean'
            )
        if required and 'default' in property_schema:
            raise ValueError(
                f'Skill {event}/{phase} additional_output property {name!r} default requires required: false'
            )
        if 'default' in property_schema and not _value_matches_schema(
            property_schema['default'], property_schema,
        ):
            raise ValueError(
                f'Skill {event}/{phase} additional_output property {name!r} default does not match its schema'
            )


def _value_matches_schema(value, schema):
    if 'enum' in schema and value not in schema['enum']:
        return False
    type_checks = {
        'object': lambda item: isinstance(item, dict),
        'array': lambda item: isinstance(item, list),
        'string': lambda item: isinstance(item, str),
        'integer': lambda item: isinstance(item, int) and not isinstance(item, bool),
        'number': lambda item: isinstance(item, (int, float)) and not isinstance(item, bool),
        'boolean': lambda item: isinstance(item, bool),
        'null': lambda item: item is None,
    }
    try:
        return type_checks[schema['type']](value)
    except KeyError:
        return False
def compact_events(events: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    """Compact related Ticket changes while retaining their first-seen order."""
    from seaqa_ai.agent.events_manager.constants import EVENT_TYPE_TICKET_COMMENT_ADDED

    grouped: dict[tuple[str | None, int | None], list[dict[str, Any]]] = {}
    ordered_keys: list[tuple[str | None, int | None]] = []
    for event in events:
        if not isinstance(event, dict):
            continue
        key = _event_merge_key(event)
        if key not in grouped:
            grouped[key] = []
            ordered_keys.append(key)
        grouped[key].append(event)

    compacted = []
    for key in ordered_keys:
        group = grouped[key]
        event_type = next(
            (
                event.get('type')
                for event in group
                if event.get('type') == EVENT_TYPE_TICKET_COMMENT_ADDED
            ),
            group[-1].get('type'),
        )
        new_value: dict[str, Any] = {}
        old_value: dict[str, Any] = {}
        for event in group:
            if isinstance(event.get('new_value'), dict):
                new_value.update(event['new_value'])
            if isinstance(event.get('old_value'), dict):
                for field, value in event['old_value'].items():
                    old_value.setdefault(field, value)
        compacted.append({
            'type': event_type,
            'old_value': old_value or None,
            'new_value': new_value or None,
        })
    return compacted


def _event_merge_key(event: dict[str, Any]) -> tuple[str | None, int | None]:
    from seaqa_ai.agent.events_manager.constants import (
        EVENT_TYPE_TICKET_COMMENT_ADDED,
        EVENT_TYPE_TICKET_COMMENT_UPDATED,
    )

    event_type = event.get('type')
    if event_type in {EVENT_TYPE_TICKET_COMMENT_ADDED, EVENT_TYPE_TICKET_COMMENT_UPDATED}:
        new_value = event.get('new_value')
        comment_id = new_value.get('_pk') if isinstance(new_value, dict) else None
        return 'ticket_comment', comment_id
    return event_type, None
