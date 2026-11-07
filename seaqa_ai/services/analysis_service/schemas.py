KNOWLEDGE_GAP_EVALUATIONS = (
    'answered',
    'knowledge_gap',
    'off_topic',
    'not_answerable',
    'invalid',
)

KNOWLEDGE_GAP_SCHEMA = {
    'type': 'object',
    'properties': {
        'evaluation': {
            'type': 'string',
            'enum': list(KNOWLEDGE_GAP_EVALUATIONS),
            'description': 'How this question should be classified.',
        },
        'reason': {
            'type': 'string',
            'description': 'Why this classification was chosen.',
        },
        'normalized_question': {
            'type': 'string',
            'description': 'A standalone question with no pronouns. Empty unless evaluation is knowledge_gap.',
        },
        'suggestion': {
            'type': 'string',
            'description': 'What knowledge to add. Empty unless evaluation is knowledge_gap.',
        },
    },
    'additionalProperties': False,
    'required': ['evaluation', 'reason', 'normalized_question', 'suggestion'],
}
