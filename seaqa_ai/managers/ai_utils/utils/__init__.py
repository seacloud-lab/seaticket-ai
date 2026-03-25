import logging

logger = logging.getLogger(__name__)

def gen_completion_format(schema, name='result_parser'):
    return {
        'type': 'json_schema',
        'json_schema': {
            'name': name,
            'strict': True,
            'schema': schema
        }
    }
