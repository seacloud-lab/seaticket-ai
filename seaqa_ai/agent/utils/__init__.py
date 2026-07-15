import html
import json

from .constants import DataEventType

EVENT_PAYLOAD_TAG = {
    DataEventType.GITHUB_ISSUE_COMMENT_ADDED.value: 'comment',
    DataEventType.DISCOURSE_TOPIC_COMMENT_ADDED.value: 'comment',
    DataEventType.EMAIL_MESSAGE_ADDED.value: 'reply',
}

EVENT_TYPE_ONLY = {
    DataEventType.TICKET_DUE_SOON.value,
    DataEventType.GITHUB_ISSUE_ADDED.value,
    DataEventType.DISCOURSE_TOPIC_ADDED.value,
    DataEventType.EMAIL_THREAD_ADDED.value,
}


def _parse_int(value) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def render_task_xml(task: dict) -> str:
    """Render a task dict as an XML-enveloped string."""

    def xml_text(value) -> str:
        return html.escape(str(value), quote=False)

    def xml_json(value) -> str:
        return xml_text(json.dumps(value, ensure_ascii=False))

    parts = []

    if 'source_id' in task:
        parts.append(f'<source_id>{task["source_id"]}</source_id>')

    if 'event' in task:
        event = task['event']
        if not isinstance(event, dict):
            parts.append(f'<event>{xml_json(event)}</event>')
        else:
            event_type = event.get('type')
            if event_type in EVENT_TYPE_ONLY:
                parts.append(f'<event>{xml_text(event_type or "")}</event>')
            else:
                payload_tag = EVENT_PAYLOAD_TAG.get(event_type)
                if payload_tag and 'new_value' in event:
                    parts.append(f'<event>{xml_text(event_type)}</event>')
                    parts.append(f'<{payload_tag}>{xml_json(event.get("new_value"))}</{payload_tag}>')
                else:
                    parts.append(f'<event>{xml_json(event)}</event>')

    if 'record' in task:
        parts.append(f'<record>{xml_json(task["record"])}</record>')

    if task.get('event_summary'):
        parts.append(f'<event_summary>{xml_text(task["event_summary"])}</event_summary>')

    if task.get('search_results'):
        parts.append(f'<search_results>{xml_json(task["search_results"])}</search_results>')

    if task.get('analysis_summary'):
        parts.append(f'<analysis_summary>{xml_json(task["analysis_summary"])}</analysis_summary>')

    return '\n'.join(parts)


def event_merge_key(event: dict) -> tuple | None:
    if not isinstance(event, dict):
        return None
    event_type = event.get('type')
    if event_type in {DataEventType.TICKET_COMMENT_ADDED.value, DataEventType.TICKET_COMMENT_UPDATED.value}:
        new_value = event.get('new_value') or {}
        comment_id = new_value.get('_pk') if isinstance(new_value, dict) else None
        return ('ticket_comment', comment_id)
    return (event_type, None)


def compact_events(events: list[dict]) -> list[dict]:
    grouped: dict[tuple, list[dict]] = {}
    ordered_keys: list[tuple] = []
    for event in events:
        if not isinstance(event, dict):
            continue
        merge_key = event_merge_key(event)
        if merge_key not in grouped:
            grouped[merge_key] = []
            ordered_keys.append(merge_key)
        grouped[merge_key].append(event.copy())

    compacted: list[dict] = []
    for merge_key in ordered_keys:
        group = grouped.get(merge_key) or []
        if not group:
            continue

        added_type = next((
            item.get('type') for item in group
            if item.get('type') == DataEventType.TICKET_COMMENT_ADDED.value
        ), None)
        result_type = added_type or group[-1].get('type')

        merged_new_value: dict = {}
        for item in group:
            new_value = item.get('new_value')
            if isinstance(new_value, dict):
                merged_new_value.update(new_value)

        merged_old_value: dict = {}
        for item in group:
            old_value = item.get('old_value')
            if not isinstance(old_value, dict):
                continue
            for key, value in old_value.items():
                if key not in merged_old_value:
                    merged_old_value[key] = value

        compacted.append({
            'type': result_type,
            'old_value': merged_old_value or None,
            'new_value': merged_new_value or None,
        })
    return compacted
