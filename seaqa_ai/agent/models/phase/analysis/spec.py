from pathlib import Path

from seaqa_ai.agent.models.phase import AgentPhaseSpec


class AnalysisPhaseSpec(AgentPhaseSpec):
    """Specification for the record analysis phase."""

    final_output_schema = {
        'type': 'object',
        'properties': {
            'analysis_report': {
                'type': 'string',
                'description': (
                    'Detailed analysis of the issue: what it is about, whether it resembles '
                    'a known problem, why this conclusion was reached, and the recommended '
                    'next action. Cite search evidence with exact returned labels such as '
                    '<reference_0>. Place multiple citations directly next to each other, '
                    'for example <reference_0><reference_1>; do not combine labels or '
                    'invent labels.'
                ),
            },
            'recommended_actions': {
                'type': 'array',
                'default_enum': [
                    'suggest_modify_type',
                    'suggest_assign_labels',
                    'suggest_reply',
                    'suggest_create_ticket',
                    'suggest_move_to_spam',
                    'no_op',
                ],
                'description': (
                    'Recommended next actions. Default action names are '
                    'suggest_modify_type, suggest_assign_labels, suggest_reply, '
                    'suggest_create_ticket, suggest_move_to_spam, and no_op. Use '
                    '["no_op"] when no handling suggestion is needed. The injected '
                    'skill may define additional action names or override these defaults.'
                ),
                'items': {'type': 'string'},
            },
            'original_issue_type': {
                'type': 'string',
                'description': (
                    'Original issue type from the record, or an empty string when unavailable '
                    'or inapplicable.'
                ),
            },
            'classified_issue_type': {
                'type': 'string',
                'default_enum': [
                    'Bug',
                    'Feature',
                    'Question',
                    'Spam',
                    'Notification',
                ],
                'description': (
                    'Agent-classified issue type based on the record title and content. '
                    'Default classifications are Bug, Feature, Question, Spam, and '
                    'Notification. The injected skill may define additional classifications '
                    'or override these defaults. Use an empty string when classification is '
                    'not required.'
                ),
            },
            'reference_related_records': {
                'type': 'array',
                'description': (
                    'Successfully loaded record identities selected from Prelude event_related_records for '
                    'handling. The triggering record must remain the first entry.'
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
            'skip_handling': {
                'type': 'boolean',
                'description': 'Whether the handling phase should be skipped.',
            },
        },
        'required': [
            'analysis_report',
            'recommended_actions',
            'original_issue_type',
            'classified_issue_type',
            'reference_related_records',
            'skip_handling',
        ],
        'additionalProperties': True,
    }

    @classmethod
    def get_system_prompt(cls) -> str:
        return (Path(__file__).with_name('system_prompt.xml')).read_text(encoding='utf-8')
