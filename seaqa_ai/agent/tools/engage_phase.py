import json

from seaqa_ai.agent.models.phase.analysis.spec import AnalysisPhaseSpec
from seaqa_ai.agent.models.phase.handling.spec import HandlingPhaseSpec
from seaqa_ai.agent.models.phase.prelude.spec import PreludePhaseSpec
from seaqa_ai.agent.utils.react import AgentReACT
from seaqa_ai.utils.tools import BasicTool


class EngagePhase(BasicTool):
    """Run one isolated phase sub-agent for the current top-level Agent run."""

    tool = {
        'type': 'function',
        'name': 'engage_phase',
        'description': (
            'Run one event-processing phase and return its structured artifact. '
            'Use the artifact to decide whether and how to engage later phases.'
        ),
        'parameters': {
            'type': 'object',
            'properties': {
                'event_type': {
                    'type': 'string',
                    'description': 'The event type used to select the phase skill.',
                },
                'phase': {
                    'type': 'string',
                    'enum': ['prelude', 'analysis', 'handling'],
                    'description': 'The phase to run.',
                },
                'message_input': {
                    'type': 'string',
                    'description': 'Phase input, including applicable prior artifacts.',
                },
                'records_input': {
                    'type': 'array',
                    'description': (
                        'Record identities that the phase loads with get_record before its '
                        'first model response.'
                    ),
                    'items': {
                        'type': 'object',
                        'properties': {
                            'source_type': {'type': 'string'},
                            'connection_id': {'type': ['integer', 'null']},
                            'record_id': {'type': 'integer'},
                        },
                        'required': ['source_type', 'connection_id', 'record_id'],
                        'additionalProperties': False,
                    },
                },
            },
            'required': ['event_type', 'phase', 'message_input', 'records_input'],
            'additionalProperties': False,
        },
        'strict': True,
    }

    _PHASE_SPECS = {
        'prelude': PreludePhaseSpec,
        'analysis': AnalysisPhaseSpec,
        'handling': HandlingPhaseSpec,
    }

    def execute(
        self,
        event_type,
        phase,
        message_input,
        records_input=None,
        app=None,
        responses_client=None,
        react_tracer=None,
        context=None,
    ):
        if not isinstance(event_type, str) or not event_type.strip():
            raise ValueError('event_type must be a non-empty string')
        if not isinstance(message_input, str):
            raise TypeError('message_input must be a string')
        if phase not in self._PHASE_SPECS:
            available_phases = ', '.join(self._PHASE_SPECS)
            raise ValueError(f'Unsupported phase: {phase!r}. Available phases: {available_phases}')
        if responses_client is None:
            raise ValueError('responses_client is required to engage a phase')

        phase_cache = (context or {}).setdefault('phase_cache', {})
        phase_context = dict(context or {})
        phase_context['phase'] = phase
        phase_context['parent_trace_id'] = (context or {}).get('active_trace_id', '')
        phase_context['phase_cache'] = phase_cache
        cache_key = self._cache_key(event_type, phase, message_input, records_input or [])
        if cache_key in phase_cache:
            return phase_cache[cache_key]
        phase_spec = self._PHASE_SPECS[phase](
            event=event_type,
            phase=phase,
            message_input=message_input,
            context=phase_context,
        )
        try:
            skill = phase_spec.get_skill()
            tools = phase_spec.get_tools()
        except KeyError as error:
            raise ValueError(
                f'No {phase} skill configured for event type {event_type!r}'
            ) from error

        result = AgentReACT(
            app=app,
            responses_client=responses_client,
            system_prompt=phase_spec.get_system_prompt(),
            message_input=phase_spec.message_input,
            tools=tools,
            react_tracer=react_tracer,
            final_output_schema=phase_spec.final_output_schema,
            max_steps=phase_spec.max_steps,
            max_steps_prompt=phase_spec.max_steps_prompt,
            context=phase_spec.context,
            skill=skill,
            records_input=records_input or [],
        ).run()
        phase_cache[cache_key] = result
        return result

    @staticmethod
    def _cache_key(event_type, phase, message_input, records_input):
        return json.dumps({
            'event_type': event_type,
            'phase': phase,
            'message_input': message_input,
            'records_input': records_input,
        }, ensure_ascii=False, sort_keys=True, default=str)
