from pathlib import Path
from typing import ClassVar

from seaqa_ai.agent.models.phase import AgentPhaseSpec


class AnalysisPhaseSpec(AgentPhaseSpec):
    """Specification for the record analysis phase."""
    enable_extra_skills: ClassVar[bool] = True

    base_final_output_schema = {
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
                     'invent labels. Write a concise, natural analysis for the user using '
                     'short paragraphs. Separate distinct ideas with blank lines so the '
                     'background, evidence, conclusion, and next action do not become one '
                     'dense paragraph. Do not require headings, a fixed section order, or '
                     'lists. Do not include the event trigger time or timestamp; the frontend '
                     'presents it separately. Business dates and durations that support the '
                     'analysis may still be included. Use straight quotes around any UI labels '
                     'or feature names as '
                     'needed; output the value as Markdown-compatible prose and do not '
                     'additionally JSON-escape characters in your analysis report.'
                ),
            },
            'recommended_actions': {
                'type': 'array',
                'description': (
                    'Recommended next actions for Handling. Each value must be an action '
                    'name explicitly authorized by the injected skill. This phase schema '
                    'validates only that the value is an array of strings; the injected skill '
                    'defines the allowed action vocabulary, including event-specific actions. '
                    'Use ["no_op"] when the injected skill determines that no handling '
                    'action is needed.'
                ),
                'items': {'type': 'string'},
            },
            'skip_handling': {
                'type': 'boolean',
                'description': 'Whether the handling phase should be skipped.',
            },
        },
        'required': [
            'analysis_report',
            'recommended_actions',
            'skip_handling',
        ],
        'additionalProperties': False,
    }

    @classmethod
    def get_system_prompt(cls) -> str:
        return (Path(__file__).with_name('system_prompt.xml')).read_text(encoding='utf-8')
