import json
from seaqa_ai.agent.utils.react import AgentReACT
from seaqa_ai.agent.models.phase.prelude.spec import PreludePhaseSpec
from seaqa_ai.agent.models.phase.analysis.spec import AnalysisPhaseSpec
from seaqa_ai.agent.models.phase.handling.spec import HandlingPhaseSpec
from seaqa_ai.utils import gen_record_hash
from seaqa_ai.utils.constants import ExtraSourceType

ALL_PHASES = {
    'prelude': PreludePhaseSpec,
    'analysis': AnalysisPhaseSpec,
    'handling': HandlingPhaseSpec
}

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
        
    def _build_default_message_input(self):
        return {
            'task_id': self.task.task_id,
            'triggering_event': {
                'event_type': self.event.event_type,
                'event': self.event.event,
                'modified_time': self.event.modified_time.isoformat() if self.event.modified_time else None,
                'received_at': self.event.received_at.isoformat(),
            },
        }

    def _start_prelude(self):
        target_record = {
            'source_type': self.task.target_source_type,
            'connection_id': self.task.target_connection_id,
            'record_id': self.task.target_record_id,
        }
        linked_ticket_identity = self.context.get('linked_ticket_identity')

        prelude_result = self._engage_phase(
            'prelude', 
            self._build_default_message_input(),
            [target_record],
        )

        event_related_records = self._build_event_related_records(
            target_record,
            linked_ticket_identity,
            prelude_result.get('event_related_records', []),
        )
        prelude_result['event_related_records'] = event_related_records
        self.result['prelude'] = prelude_result

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

    def _build_event_related_records(
        self,
        target_record,
        linked_ticket_identity,
        selected_records,
    ):
        target_key = self._record_key(target_record)
        linked_ticket_key = self._record_key(linked_ticket_identity)
        selected_records = selected_records if isinstance(selected_records, list) else []

        if linked_ticket_key is None:
            records = [target_record]
            seen_records = {target_key}
            for record in selected_records:
                record_key = self._record_key(record)
                if record_key in self.record_cache and record_key not in seen_records:
                    seen_records.add(record_key)
                    records.append(record)
            return records

        linked_ticket = self.context.get('linked_ticket_record') or {}
        allowed_records = {
            linked_record
            for linked_record in linked_ticket.get('linked_connection_records', [])
            if isinstance(linked_record, str)
        }
        linked_ticket_selected = any(
            self._record_key(record) == linked_ticket_key
            for record in selected_records
        )
        if not linked_ticket_selected or linked_ticket_key not in self.record_cache:
            return [target_record]

        records = [target_record, linked_ticket_identity]
        seen_records = {target_key, linked_ticket_key}
        for record in selected_records:
            record_key = self._record_key(record)
            if (
                self._linked_connection_record_key(record) in allowed_records
                and record_key in self.record_cache
                and record_key not in seen_records
            ):
                seen_records.add(record_key)
                records.append(record)
        return records

    @staticmethod
    def _linked_connection_record_key(record):
        record_key = AgentPhasePromoter._record_key(record)
        if record_key is None:
            return None
        _, connection_id, record_id = record_key
        if connection_id is None:
            return None
        return f'{connection_id}_{record_id}'

    def _build_event_records_input(self, event_related_records):
        linked_ticket_identity = self.context.get('linked_ticket_identity')
        if linked_ticket_identity is None or not any(
            self._record_key(record) == self._record_key(linked_ticket_identity)
            for record in event_related_records
        ):
            return event_related_records
        target_record = {
            'source_type': self.task.target_source_type,
            'connection_id': self.task.target_connection_id,
            'record_id': self.task.target_record_id,
        }
        linked_ticket_key = self._record_key(linked_ticket_identity)
        other_records = [
            record for record in event_related_records
            if self._record_key(record) not in {self._record_key(target_record), linked_ticket_key}
        ]
        return {
            'PreStep 1: Inject the record that triggered the event': [target_record],
            'PreStep 2: Inject the Ticket linked by the triggering record': [
                linked_ticket_identity,
            ],
            'PreStep 3: Inject other verified records linked to that Ticket': other_records,
        }

    @staticmethod
    def _build_reference_related_records(sources):
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
            self._build_event_records_input(event_related_records),
        )
        sources = analysis_result.get('sources', [])
        analysis_result['reference_related_records'] = self._build_reference_related_records(
            sources,
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
        
        records_input = self._build_event_records_input(event_related_records)
        if isinstance(records_input, dict):
            records_input['PreStep 4: Inject records related to the analysis report'] = (
                reference_related_records
            )
        else:
            records_input = {
                'PreStep 1: Inject records related to the event': records_input,
                'PreStep 2: Inject records related to the analysis report': reference_related_records,
            }

        self.result['handling'] = self._engage_phase(
            'handling', 
            message_input,
            records_input,
        )

    def _engage_phase(self, phase, message_input, records_input=None):
        if not isinstance(message_input, str):
            message_input = json.dumps(message_input)

        phase_context = dict(self.context or {})
        phase_context['phase'] = phase
        phase_spec = ALL_PHASES[phase](
            event=self.event.event_type,
            phase=phase,
            message_input=message_input,
            records_input=records_input,
            context=phase_context,
        )
        try:
            skill = phase_spec.get_skill()
            tools = phase_spec.get_tools()
        except KeyError as error:
            raise ValueError(
                f'No {phase} skill configured for event type {self.event.event_type!r}'
            ) from error

        system_prompts = [phase_spec.get_system_prompt()]
        if self.project_prompt and self.project_prompt.strip():
            system_prompts.append(self.project_prompt)

        react = AgentReACT(
            app=self.app,
            responses_client=self.responses_client,
            system_prompts=system_prompts,
            message_input=phase_spec.message_input,
            records_input=phase_spec.records_input,
            tools=tools,
            react_tracer=self.react_tracer,
            final_output_schema=phase_spec.final_output_schema,
            max_steps=phase_spec.max_steps,
            max_steps_prompt=phase_spec.max_steps_prompt,
            context=phase_spec.context,
            skill=skill
        )
        result = react.run()
        return result
