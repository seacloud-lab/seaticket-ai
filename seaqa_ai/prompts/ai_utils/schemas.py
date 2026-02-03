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
    }
}
