import logging
from seaqa_ai.utils import gen_attachment_hex
from seaqa_ai.utils.constants import ExtraSourceType
from seaqa_ai.utils.seadb_api import gen_seadb_table_name

logger = logging.getLogger(__name__)

def search_records_to_map(search_records, search_results, attachments_hex=[]):
    """
    func:  Deduplication and format search_records to a map:
    {
        'connection_id': {
            'record_id': record # 'record_id', i.e., _pk in seadb, _id in seaqa-indexer
        }
    }
    connection_id_type_map: a map of {
        'connection_id': 'connection_type'
    }
    i.e., search_results on other funcs
    """
    connection_id_record_id_record_map = search_results
    for record in search_records:
        try:
            connection_id = int(record.get('connection_id', 'unknown'))
        except:
            connection_id = record.get('connection_id', 'unknown')
        if record['type'] in ExtraSourceType:
            connection_id = record['type']

        try:
            record_id = int(record['_id'])
        except:
            record_id = record['_id']

        if gen_attachment_hex(record['type'], connection_id, record_id) in attachments_hex:
            continue

        connection_dict = connection_id_record_id_record_map.setdefault(connection_id, {})

        if 'snippet' in record:
            if existing_record := connection_dict.get(record_id):
                snippets = existing_record.get('snippets', [])
            else:
                snippets = []
            record['snippets'] = snippets
            record['snippets'].append(record['snippet'])

        connection_id_record_id_record_map[connection_id][record_id] = record
    return connection_id_record_id_record_map

def get_title_and_ai_summary_of_search_results(search_results, seadb_api, project_uuid, connection_id_type_map):
    """
    func type: retrieve title and ai_summary from seadb

    Args: 
    search_results: a map of {
        'connection_id': {
            'record_id': record # 'record_id', i.e., _pk in seadb, _id in seaqa-indexer
        }
    }
    connection_id_type_map: a map of {
        'connection_id': 'connection_type'
    }

    search_results also present as return

    The notes of this function see KNOWLEDGE_BASE as example
    """

    for connection_id, record_id_record_map in search_results.items():
        connection_type = connection_id_type_map[connection_id]
        connection_table_name, _ = gen_seadb_table_name(connection_type, connection_id)

        seadb_record_id_record_comments_map = seadb_api.get_record_and_comments(project_uuid, record_id_record_map.keys(), connection_table_name, record_fields_str='title, ai_summary')

        for record_id in list(record_id_record_map.keys()): # use list() to copy keys for avoiding iter error
            if record_id not in seadb_record_id_record_comments_map:
                del record_id_record_map[record_id] # --> deleted record, do not sent to AI
                continue

            record_id_record_map[record_id]['title'] = seadb_record_id_record_comments_map[record_id]['record']['title']
            record_id_record_map[record_id]['ai_summary'] = seadb_record_id_record_comments_map[record_id]['record']['ai_summary']
    
    for connection_id in list(search_results.keys()):
        if not search_results[connection_id]:
            del search_results[connection_id]

    return search_results
