import json

from seaqa_ai.agent.agent_service.constants import LINKED_TICKET_TRACE_EVENT_TYPES
from seaqa_ai.agent.models.phase import AgentPhaseSpec
from seaqa_ai.agent.models.phase.analysis.spec import AnalysisPhaseSpec
from seaqa_ai.agent.models.phase.handling.spec import HandlingPhaseSpec
from seaqa_ai.agent.models.phase.prelude.spec import PreludePhaseSpec
from seaqa_ai.agent.models.phase.regeneration.spec import RegenerationPhaseSpec
from seaqa_ai.agent.utils.react import AgentReACT
from seaqa_ai.agent.utils.record_identity import validate_record_identity
from seaqa_ai.utils import gen_record_hash
from seaqa_ai.utils.constants import ExtraSourceType


ALL_PHASES = {
    'prelude': PreludePhaseSpec,
    'analysis': AnalysisPhaseSpec,
    'handling': HandlingPhaseSpec,
    'regeneration': RegenerationPhaseSpec,
}


class _RecordInputFormatter:
    @staticmethod
    def target_record_identity(task):
        return {
            'source_type': task.target_item_type,
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
    def build_records_with_references(event_records_input, reference_related_records):
        """Append Analysis references after event records in injection order."""
        if isinstance(event_records_input, dict):
            # Linked-ticket input already groups the event records into three steps.
            return {
                **event_records_input,
                'PreStep 4: Inject records related to the analysis report': (
                    reference_related_records
                ),
            }
        return {
            'PreStep 1: Inject records related to the event': event_records_input,
            'PreStep 2: Inject records related to the analysis report': (
                reference_related_records
            ),
        }

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

    def start_regeneration(
        self,
        user_instruction,
        previous_drafts=None,
        error_message=None,
        resume_from='regeneration',
    ):
        if resume_from == 'prelude':
            self._start_prelude()
            self._start_analysis()
        elif resume_from == 'analysis':
            self._start_analysis()
        self._start_regeneration(user_instruction, previous_drafts, error_message)

    def _start_prelude(self):
        target_record = _RecordInputFormatter.target_record_identity(self.task)
        linked_ticket_identity = self.context.get('linked_ticket_identity')
        prelude_output = self._engage_phase(
            'prelude',
            self._build_default_message_input(),
            _RecordInputFormatter.build_prelude_records_input(
                self.task, self.event, self.context,
            ),
        )
        prelude_output['event_related_records'] = (
            _RecordInputFormatter.build_event_related_records(
                target_record,
                linked_ticket_identity,
                prelude_output.get('event_related_records', []),
                self.context,
                self.record_cache,
            )
        )
        self.result['prelude'] = prelude_output

    def _start_analysis(self):
        prelude_output = self.result.get('prelude', {})
        event_related_records = prelude_output.get('event_related_records', [])
        # These records are already injected, so searches should exclude them.
        self.context['skip_search_record_hashes'] = [
            gen_record_hash(
                record['source_type'], record['connection_id'], record['record_id'],
            )
            for record in event_related_records
        ]

        message_input, records_input = self._build_event_inputs()
        message_input.update(self._declared_additional_output('prelude', prelude_output))

        analysis_output = self._engage_phase(
            'analysis', message_input, records_input,
        )
        # Retain reference identities for injection in Handling and Regeneration.
        analysis_output['reference_related_records'] = (
            _RecordInputFormatter.build_reference_related_records(
                analysis_output.get('sources', []),
            )
        )
        self.result['analysis'] = analysis_output

    def _start_handling(self):
        analysis_output = self.result.get('analysis', {})
        if analysis_output.get('skip_handling'):
            return

        message_input, records_input = self._build_inputs_with_analysis()
        recommended_actions = analysis_output.get('recommended_actions', [])
        if recommended_actions is not None:
            # Keep only Analysis-recommended actions allowed by the Handling skill.
            handling_tools = AgentPhaseSpec.get_skill_metadata(
                self.event.event_type, 'handling',
            ).get('tools', [])
            # no_op represents taking no action; it does not require a tool.
            allowed_actions = set(handling_tools) | {'no_op'}
            if not isinstance(recommended_actions, list):
                recommended_actions = []
            message_input['recommended_actions'] = [
                action
                for action in recommended_actions
                if isinstance(action, str) and action in allowed_actions
            ]
        message_input.update(self._declared_additional_output('analysis', analysis_output))

        self.result['handling'] = self._engage_phase(
            'handling', message_input, records_input,
        )

    def _start_regeneration(self, user_instruction, previous_drafts=None, error_message=None):
        analysis_output = self.result.get('analysis', {})
        regeneration_tools = AgentPhaseSpec.get_skill_metadata(
            self.event.event_type, 'regeneration',
        ).get('tools', [])

        message_input, records_input = self._build_inputs_with_analysis()
        # The user can request any tool allowed by the Regeneration skill,
        # including actions that Analysis did not originally recommend.
        message_input['recommended_actions'] = [
            tool_name
            for tool_name in regeneration_tools
            if isinstance(tool_name, str) and tool_name
        ]
        if 'skip_handling' in analysis_output:
            # Preserve the original decision as context, without skipping regeneration.
            message_input['skip_handling'] = analysis_output.get('skip_handling')
        message_input['user_instruction'] = str(user_instruction or '').strip()
        compacted_drafts = self._compact_previous_drafts(previous_drafts)
        if compacted_drafts:
            message_input['previous_drafts'] = compacted_drafts
        if error_message:
            message_input['error_message'] = str(error_message)
        if analysis_output:
            message_input.update(self._declared_additional_output('analysis', analysis_output))

        self.result['regeneration'] = self._engage_phase(
            'regeneration', message_input, records_input,
        )

    def _build_event_inputs(self):
        """Build the event input shared by Analysis, Handling and Regeneration.

        Include task/event metadata, Prelude's summary and event records.
        Callers add stage-specific fields and skill-declared output.
        """
        prelude_output = self.result.get('prelude', {})
        event_summary = prelude_output.get('event_summary')
        event_related_records = prelude_output.get('event_related_records', [])

        message_input = self._build_default_message_input()
        if event_summary is not None:
            message_input['event_summary'] = event_summary
        records_input = _RecordInputFormatter.build_event_records_input(
            self.task, self.context, event_related_records,
        )
        return message_input, records_input

    def _build_inputs_with_analysis(self):
        """Extend event input with the analysis report and reference records.

        Handling and Regeneration share this context but supply their own
        action rules and other fields in their respective entry points.
        """
        message_input, records_input = self._build_event_inputs()
        analysis_output = self.result.get('analysis', {})
        analysis_report = analysis_output.get('analysis_report')
        if analysis_report is not None:
            message_input['analysis_report'] = analysis_report

        records_input = _RecordInputFormatter.build_records_with_references(
            records_input, analysis_output.get('reference_related_records', []),
        )
        return message_input, records_input

    @staticmethod
    def _compact_previous_drafts(previous_drafts):
        if not isinstance(previous_drafts, list):
            return []
        compacted = []
        for draft in previous_drafts:
            if not isinstance(draft, dict):
                continue
            payload = draft.get('suggestion_payload')
            if isinstance(payload, str):
                try:
                    payload = json.loads(payload)
                except (TypeError, ValueError):
                    payload = {}
            compacted.append({
                'tool_name': draft.get('tool_name') or '',
                'suggestion_content': draft.get('suggestion_content') or '',
                'suggestion_reason': draft.get('suggestion_reason') or '',
                'suggestion_payload': payload if isinstance(payload, dict) else {},
                'target_item_type': draft.get('target_item_type') or '',
                'target_item_id': draft.get('target_item_id') or '',
                'target_item_title': draft.get('target_item_title') or '',
            })
        return compacted

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

    def _declared_additional_output(self, phase, result):
        """Select skill-declared output, excluding the source phase's base fields.

        Callers merge these fields last to preserve their precedence over
        other prepared message fields.
        """
        additional_output = AgentPhaseSpec.get_skill_metadata(
            self.event.event_type, phase,
        ).get('additional_output', {})
        base_fields = ALL_PHASES[phase].base_final_output_schema.get('properties', {})
        return {
            field: result[field]
            for field in additional_output
            if field not in base_fields and field in result
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
