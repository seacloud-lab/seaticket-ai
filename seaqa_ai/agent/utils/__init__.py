import importlib
import inspect
import re
from collections.abc import Iterable
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml

from seaqa_ai.skills.parser import split_skill_document
from seaqa_ai.utils.tools import BasicTool
from seaqa_ai.utils.tools.search_tools import DocumentsSearch, IssuesSearch, TasksSearch
from seaqa_ai.utils.tools.get_records import GetRecords



TOOLS_PACKAGE = 'seaqa_ai.agent.tools'
TOOLS_DIR = Path(__file__).resolve().parent.parent / 'tools'
SKILLS_DIR = Path(__file__).resolve().parent.parent / 'skills'


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
        'issues_search': IssuesSearch,
        'tasks_search': TasksSearch,
        'get_records': GetRecords,
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
            skill = re.sub(r'\bget_record\b', 'get_records', skill_file.read_text(encoding='utf-8'))
            _validate_skill(skill, event_dir.name, skill_file.stem)
            phases[skill_file.stem] = skill

        if phases:
            skills_map[event_dir.name] = phases

    return skills_map


def _validate_skill(skill, event, phase):
    try:
        metadata_text, _ = split_skill_document(skill)
    except ValueError as error:
        raise ValueError(f'Skill {event}/{phase} must contain the required metadata separator') from error
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
    if phase not in ('prelude', 'analysis') and 'get_records' in tool_names:
        raise ValueError(f'Only prelude and analysis skills may use get_records: {event}/{phase}')

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
