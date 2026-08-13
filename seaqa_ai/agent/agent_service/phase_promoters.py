import json

from seaqa_ai.agent.agent_service.constants import LINKED_TICKET_TRACE_EVENT_TYPES
from seaqa_ai.agent.models.phase.analysis.spec import AnalysisPhaseSpec
from seaqa_ai.agent.models.phase.handling.spec import HandlingPhaseSpec
from seaqa_ai.agent.models.phase.prelude.spec import PreludePhaseSpec
from seaqa_ai.agent.utils.react import AgentReACT
from seaqa_ai.agent.utils.record_identity import validate_record_identity
from seaqa_ai.utils import gen_record_hash
from seaqa_ai.utils.constants import ExtraSourceType


ALL_PHASES = {
    'prelude': PreludePhaseSpec,
    'analysis': AnalysisPhaseSpec,
    'handling': HandlingPhaseSpec,
}


class _RecordInputFormatter:
    @staticmethod
    def target_record_identity(task):
        return {
            'source_type': task.target_source_type,
            'connection_id': task.target_connection_id,
            'record_id': task.target_record_id,
        }

    @staticmethod
    def _record_key(record):
        if not isinstance(record, dict):
            return None
        source_type = record.get('source_type')
        connection_id = record.get('connection_id')
        record_id = record.get('record_id')
        if (
            not isinstance(source_type, str)
            or not source_type
            or (connection_id is not None and not isinstance(connection_id, int))
            or not isinstance(record_id, int)
        ):
            return None
        return source_type, connection_id, record_id

    @staticmethod
    def _linked_connection_record_key(record):
        record_key = _RecordInputFormatter._record_key(record)
        if record_key is None:
            return None
        _, connection_id, record_id = record_key
        if connection_id is None:
            return None
        return f'{connection_id}_{record_id}'

    @staticmethod
    def _has_valid_linked_ticket_identity(context):
        linked_ticket_identity = context.get('linked_ticket_identity')
        if _RecordInputFormatter._record_key(linked_ticket_identity) is None:
            return False
        try:
            validate_record_identity(
                linked_ticket_identity['source_type'],
                linked_ticket_identity['connection_id'],
                linked_ticket_identity['record_id'],
            )
        except ValueError:
            return False
        return True

    @staticmethod
    def _connection_source_types(context):
        source_types = {}
        for connection in (
            context.get('document_connections', [])
            + context.get('issue_connections', [])
            + context.get('task_connections', [])
        ):
            if not isinstance(connection, dict):
                continue
            connection_id = connection.get('id')
            source_type = connection.get('type')
            if isinstance(connection_id, int) and isinstance(source_type, str):
                source_types[connection_id] = source_type
        return source_types

    @staticmethod
    def _build_linked_ticket_sibling_records(task, context):
        linked_ticket = context.get('linked_ticket_record') or {}
        source_types = _RecordInputFormatter._connection_source_types(context)
        records = []
        seen_records = {
            _RecordInputFormatter._record_key(
                _RecordInputFormatter.target_record_identity(task),
            ),
        }
        linked_ticket_key = _RecordInputFormatter._record_key(
            context['linked_ticket_identity'],
        )
        for linked_record in linked_ticket.get('linked_connection_records', []):
            if not isinstance(linked_record, str):
                continue
            try:
                connection_id, record_id = linked_record.split('_', 1)
                connection_id = int(connection_id)
                record_id = int(record_id)
            except (TypeError, ValueError):
                continue
            source_type = source_types.get(connection_id)
            if source_type is None:
                continue
            try:
                source_type = validate_record_identity(source_type, connection_id, record_id)
            except ValueError:
                continue
            record = {
                'source_type': source_type,
                'connection_id': connection_id,
                'record_id': record_id,
            }
            record_key = _RecordInputFormatter._record_key(record)
            if record_key in seen_records or record_key == linked_ticket_key:
                continue
            seen_records.add(record_key)
            records.append(record)
        return records

    @staticmethod
    def _build_linked_ticket_records_input(task, context, other_records):
        return {
            'PreStep 1: Inject the record that triggered the event': [
                _RecordInputFormatter.target_record_identity(task),
            ],
            'PreStep 2: Inject the Ticket linked by the triggering record': [
                context['linked_ticket_identity'],
            ],
            'PreStep 3: Inject other verified records linked to that Ticket': other_records,
        }

    @staticmethod
    def build_prelude_records_input(task, event, context):
        if (
            event.event_type not in LINKED_TICKET_TRACE_EVENT_TYPES
            or not _RecordInputFormatter._has_valid_linked_ticket_identity(context)
        ):
            return [_RecordInputFormatter.target_record_identity(task)]
        return _RecordInputFormatter._build_linked_ticket_records_input(
            task,
            context,
            _RecordInputFormatter._build_linked_ticket_sibling_records(task, context),
        )

    @staticmethod
    def build_event_related_records(
        target_record, linked_ticket_identity, selected_records, context, record_cache,
    ):
        target_key = _RecordInputFormatter._record_key(target_record)
        linked_ticket_key = _RecordInputFormatter._record_key(linked_ticket_identity)
        selected_records = selected_records if isinstance(selected_records, list) else []

        if linked_ticket_key is None:
            records = [target_record]
            seen_records = {target_key}
            for record in selected_records:
                record_key = _RecordInputFormatter._record_key(record)
                if record_key in record_cache and record_key not in seen_records:
                    seen_records.add(record_key)
                    records.append(record)
            return records

        linked_ticket = context.get('linked_ticket_record') or {}
        allowed_records = {
            linked_record
            for linked_record in linked_ticket.get('linked_connection_records', [])
            if isinstance(linked_record, str)
        }
        linked_ticket_selected = any(
            _RecordInputFormatter._record_key(record) == linked_ticket_key
            for record in selected_records
        )
        if not linked_ticket_selected or linked_ticket_key not in record_cache:
            return [target_record]

        records = [target_record, linked_ticket_identity]
        seen_records = {target_key, linked_ticket_key}
        for record in selected_records:
            record_key = _RecordInputFormatter._record_key(record)
            if (
                _RecordInputFormatter._linked_connection_record_key(record) in allowed_records
                and record_key in record_cache
                and record_key not in seen_records
            ):
                seen_records.add(record_key)
                records.append(record)
        return records

    @staticmethod
    def build_event_records_input(task, context, event_related_records):
        if not _RecordInputFormatter._has_valid_linked_ticket_identity(context):
            return event_related_records
        linked_ticket_identity = context['linked_ticket_identity']
        linked_ticket_key = _RecordInputFormatter._record_key(linked_ticket_identity)
        if not any(
            _RecordInputFormatter._record_key(record) == linked_ticket_key
            for record in event_related_records
        ):
            return event_related_records
        target_key = _RecordInputFormatter._record_key(
            _RecordInputFormatter.target_record_identity(task),
        )
        other_records = [
            record
            for record in event_related_records
            if _RecordInputFormatter._record_key(record) not in {
                target_key, linked_ticket_key,
            }
        ]
        return _RecordInputFormatter._build_linked_ticket_records_input(
            task, context, other_records,
        )

    @staticmethod
    def build_reference_related_records(sources):
        records = []
        seen_records = set()
        for source in sources:
            if not isinstance(source, dict):
                continue
            source_type = source.get('type')
            connection_id = source.get('connection_id')
            record_id = source.get('_id')
            if not isinstance(source_type, str) or not isinstance(record_id, int):
                continue
            if ExtraSourceType.is_valid(source_type):
                connection_id = None
            elif not isinstance(connection_id, int):
                continue
            record_key = source_type, connection_id, record_id
            if record_key in seen_records:
                continue
            seen_records.add(record_key)
            records.append({
                'source_type': source_type,
                'connection_id': connection_id,
                'record_id': record_id,
            })
        return records


class AgentPhasePromoter:
    def __init__(self, app, task, responses_client, react_tracer, context, record_cache,
                 project_prompt=''):
        self.app = app
        self.task = task
        self.event = task.trigger_event
        self.responses_client = responses_client
        self.react_tracer = react_tracer
        self.context = context
        self.record_cache = record_cache
        self.project_prompt = project_prompt
        self.result = {}

    def start(self):
        self._start_prelude()
        self._start_analysis()
        self._start_handling()

    def _start_prelude(self):
        target_record = _RecordInputFormatter.target_record_identity(self.task)
        linked_ticket_identity = self.context.get('linked_ticket_identity')
        prelude_result = self._engage_phase(
            'prelude',
            self._build_default_message_input(),
            _RecordInputFormatter.build_prelude_records_input(
                self.task, self.event, self.context,
            ),
        )
        prelude_result['event_related_records'] = (
            _RecordInputFormatter.build_event_related_records(
                target_record,
                linked_ticket_identity,
                prelude_result.get('event_related_records', []),
                self.context,
                self.record_cache,
            )
        )
        self.result['prelude'] = prelude_result

    def _start_analysis(self):
        prelude_result = self.result.get('prelude', {})
        event_summary = prelude_result.get('event_summary')
        event_related_records = prelude_result.get('event_related_records', [])
        self.context['skip_search_record_hashes'] = [
            gen_record_hash(
                record['source_type'], record['connection_id'], record['record_id'],
            )
            for record in event_related_records
        ]

        message_input = self._build_default_message_input()
        if event_summary is not None:
            message_input['event_summary'] = event_summary

        analysis_result = self._engage_phase(
            'analysis',
            message_input,
            _RecordInputFormatter.build_event_records_input(
                self.task, self.context, event_related_records,
            ),
        )
        analysis_result['reference_related_records'] = (
            _RecordInputFormatter.build_reference_related_records(
                analysis_result.get('sources', []),
            )
        )
        self.result['analysis'] = analysis_result

    def _start_handling(self):
        prelude_result = self.result.get('prelude', {})
        event_summary = prelude_result.get('event_summary')
        event_related_records = prelude_result.get('event_related_records', [])

        analysis_result = self.result.get('analysis', {})
        if analysis_result.get('skip_handling'):
            return
        analysis_report = analysis_result.get('analysis_report')
        original_issue_type = analysis_result.get('original_issue_type')
        classified_issue_type = analysis_result.get('classified_issue_type')
        recommended_actions = analysis_result.get('recommended_actions', [])
        related_ticket = analysis_result.get('related_ticket')
        reference_related_records = analysis_result.get('reference_related_records', [])

        message_input = self._build_default_message_input()
        if event_summary is not None:
            message_input['event_summary'] = event_summary
        if analysis_report is not None:
            message_input['analysis_report'] = analysis_report
        if original_issue_type is not None:
            message_input['original_issue_type'] = original_issue_type
        if classified_issue_type is not None:
            message_input['classified_issue_type'] = classified_issue_type
        if recommended_actions is not None:
            message_input['recommended_actions'] = recommended_actions
        if related_ticket is not None:
            message_input['related_ticket'] = related_ticket

        records_input = _RecordInputFormatter.build_event_records_input(
            self.task, self.context, event_related_records,
        )
        if isinstance(records_input, dict):
            records_input['PreStep 4: Inject records related to the analysis report'] = (
                reference_related_records
            )
        else:
            records_input = {
                'PreStep 1: Inject records related to the event': records_input,
                'PreStep 2: Inject records related to the analysis report': (
                    reference_related_records
                ),
            }

        self.result['handling'] = self._engage_phase(
            'handling',
            message_input,
            records_input,
        )

    def _build_default_message_input(self):
        return {
            'task_id': self.task.task_id,
            'triggering_event': {
                'event_type': self.event.event_type,
                'event': self.event.event,
                'modified_time': (
                    self.event.modified_time.isoformat()
                    if self.event.modified_time else None
                ),
                'received_at': self.event.received_at.isoformat(),
            },
        }

    def _engage_phase(self, phase, message_input, records_input=None):
        if not isinstance(message_input, str):
            message_input = json.dumps(message_input)

        phase_spec = ALL_PHASES[phase](
            event=self.event.event_type,
            phase=phase,
            message_input=message_input,
            records_input=records_input,
            context=self.context,
        )
        react = AgentReACT(
            app=self.app,
            responses_client=self.responses_client,
            phase_spec=phase_spec,
            react_tracer=self.react_tracer,
            project_prompt=self.project_prompt,
        )
        return react.run()
