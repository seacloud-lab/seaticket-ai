SCHEMAS_MAP = {
    'generate_ticket': {
        'type': 'object',
        'properties': {
            'title': {
                'type': 'string',
                'description': 'New Ticket Title'
            },
            'description': {
                'type': 'string',
                'description': 'A detailed summary of the ticket body'
            }
        },
        'additionalProperties': False,
        'required': ['title', 'description']
    },
    'generate_kb_record_from_ticket': {
        'type': 'object',
        'properties': {
            'kb_title': {
                'type': 'string',
                'description': 'Knowledge base record title'
            },
            'kb_content': {
                'type': 'string',
                'description': 'Knowledge base record content'
            }
        },
        'additionalProperties': False,
        'required': ['kb_title', 'kb_content']
    },
    'rank_relevant_records': {
        'type': 'object',
        'properties': {
            'results': {
                'type': 'array',
                'description': 'The relevant record keys ordered from most relevant to least relevant. If no records are relevant, set it to an empty array: []',
                'items': {
                    'type': 'object',
                    'description': 'The key of a record',
                    'properties': {
                        'connection_id': {
                            'type': 'string',
                            'description': 'the connection_id field of the candidate record'
                        },
                        'record_id': {
                            'type': 'string',
                            'description': 'the record_id field of the candidate record'
                        },
                    },
                    'required': ['connection_id', 'record_id'],
                    'additionalProperties': False
                }
            }
        },
        'additionalProperties': False,
        'required': ['results']
    },
    'generate_chat_title': {
        'type': 'object',
        'properties': {
            'title': {
                'type': 'string',
                'description': 'A concise chat title'
            }
        },
        'additionalProperties': False,
        'required': ['title']
    },
    'validate_chat_input': {
        'type': 'object',
        'properties': {
            'valid': {
                'type': 'boolean',
                'description': 'Whether the chat request may proceed.'
            },
            'reason': {
                'type': 'string',
                'description': 'A brief user-facing explanation'
            }
        },
        'additionalProperties': False,
        'required': ['valid', 'reason']
    }
}
