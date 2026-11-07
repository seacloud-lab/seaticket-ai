"""Shared pending-suggestion action behavior for agent tools."""

from seaqa_ai.utils.tools import BasicTool
from seaqa_ai.utils.record.identity import (
    ALL_SOURCES_TYPES,
    validate_record_identity,
)


class BasicAgentSuggestionTool(BasicTool):
    """Build trace descriptors for suggestions without exposing action identity to the model."""

    action_type = 'suggestion'
    action_status = 'pending'
    suggestion_content = None

    @staticmethod
    def _target_from_context(context):
        context = context or {}
        return {
            'source_type': context.get('source_type', ''),
            'source_id': context.get('source_id', ''),
            'source_title': context.get('source_title', ''),
        }

    @staticmethod
    def _target_from_trigger_context(context):
        context = context or {}
        return {
            'source_type': context.get('trigger_source_type', context.get('source_type', '')),
            'source_id': context.get('trigger_source_id', context.get('source_id', '')),
            'source_title': context.get('trigger_source_title', context.get('source_title', '')),
        }

    @classmethod
    def _normalize_target(cls, target):
        if not isinstance(target, dict):
            raise ValueError('target must be an object')
        source_type = str(target.get('source_type', '') or '').strip()
        source_id = target.get('source_id')
        record_id = target.get('record_id')
        connection_id = target.get('connection_id')
        if source_type not in ALL_SOURCES_TYPES:
            available_source_types = ', '.join(ALL_SOURCES_TYPES)
            raise ValueError(
                f'target source_type must be one of: {available_source_types}'
            )
        if source_id not in (None, ''):
            source_id = str(source_id).strip()
        elif record_id not in (None, ''):
            validate_record_identity(source_type, connection_id, record_id)
            if connection_id is not None:
                source_id = f'{connection_id}_{record_id}'
            else:
                source_id = str(record_id)
        else:
            raise ValueError('target requires source_id or record_id')
        if not source_id:
            raise ValueError('target source_id must not be empty')
        return {
            'source_type': source_type,
            'source_id': source_id,
            'source_title': str(target.get('source_title', '') or '').strip(),
        }

    @classmethod
    def _loaded_target(cls, target, context, required_source_type):
        context = context or {}
        if target is None:
            target = {
                'source_type': context.get('source_type'),
                'source_id': context.get('source_id'),
            }
        normalized_target = cls._normalize_target(target)
        if normalized_target['source_type'] != required_source_type:
            raise ValueError(f'target must be a {required_source_type}')

        record = cls._loaded_records(context).find_by_source_id(
            required_source_type, normalized_target['source_id'],
        )
        if not isinstance(record, dict):
            raise ValueError(f'target is not an available loaded {required_source_type}')
        normalized_target['source_title'] = str(record.get('title', '') or '').strip()
        return normalized_target

    @staticmethod
    def _loaded_records(context):
        loaded_records = (context or {}).get('loaded_records')
        if loaded_records is None:
            raise ValueError('The request record cache is not initialized')
        return loaded_records

    @staticmethod
    def _linked_source_ids(context):
        linked_ticket = (context or {}).get('linked_ticket_record') or {}
        linked_records = linked_ticket.get('linked_connection_records') or []
        return {
            linked_record
            for linked_record in linked_records
            if isinstance(linked_record, str) and linked_record.strip()
        }

    @classmethod
    def _linked_external_targets(cls, targets, context):
        linked_source_ids = cls._linked_source_ids(context)
        loaded_records = cls._loaded_records(context)
        trigger_source_id = str((context or {}).get('trigger_source_id', '') or '').strip()
        normalized_targets = []
        seen_source_ids = set()
        for target in targets:
            normalized_target = cls._normalize_target(target)
            source_id = normalized_target['source_id']
            if (
                source_id not in linked_source_ids
                or source_id == trigger_source_id
                or source_id in seen_source_ids
            ):
                continue
            record = loaded_records.find_by_source_id(
                normalized_target['source_type'], source_id,
            )
            if not isinstance(record, dict):
                continue
            seen_source_ids.add(source_id)
            normalized_target['source_title'] = str(record.get('title', '') or '').strip()
            normalized_targets.append(normalized_target)
        return normalized_targets

    def suggestion_result(
        self, *, suggestion_content='', context=None, targets=None, suggestion_reason='',
        tool_name=None, suggestion_payload=None,
    ):
        if targets is None:
            targets = [self._target_from_context(context)]
        normalized_targets = [self._normalize_target(target) for target in targets]
        return {
            'status': self.action_status,
            'suggestion_content': self.suggestion_content if suggestion_content is None else suggestion_content,
            'suggestion_reason': str(suggestion_reason or '').strip(),
            'suggestion_payload': suggestion_payload if isinstance(suggestion_payload, dict) else {},
            'trace_targets': normalized_targets,
            'trace_tool_name': tool_name or self.tool.get('name'),
        }
