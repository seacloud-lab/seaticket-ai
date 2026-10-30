import logging
from seaqa_ai.utils import gen_record_hash
from seaqa_ai.utils.constants import ExtraSourceType
from seaqa_ai.seadb_models.utils import get_connection_table_name, SCHEMA_TABLE_CONNECTION, EXTRA_SOURCE_TABLE_MAP

logger = logging.getLogger(__name__)

SEARCH_METADATA_FIELDS = ('url', 'state', 'state_reason', 'substate', 'resolved', 'linked_ticket')


def search_records_to_map(search_records, search_results, skip_search_record_hashes=[]):
    """
    func:  convert a search_records (from seaqa-indexer) to a map {conection_id: {record_id: ...}} (i.e., search_results)

    search_records: search API response from seaqa-indexer

    search_results: previous search results

    skip_search_record_hashes: If the hash value of a search record appears in this list, it will not be recorded.
    """
    for record in search_records:
        try:
            connection_id = int(record.get('connection_id', 'unknown'))
        except:
            connection_id = record.get('connection_id', 'unknown')
        is_extra_source = ExtraSourceType.is_valid(record['type'])
        if is_extra_source:
            connection_id = record['type']

        try:
            record_id = int(record['_id'])
        except:
            record_id = record['_id']

        # Ignore if the record in skip record list
        record_hex = gen_record_hash(
            record['type'], None if is_extra_source else connection_id, record_id,
        )
        if record_hex in skip_search_record_hashes:
            continue

        # Retrieve or create a dictionary for the given connection_id from search_results
        connection_dict = search_results.setdefault(connection_id, {})

        # Retrieve or create a dictionary for the search record
        record_dict = connection_dict.setdefault(record_id, {
            'type': record['type'],
            'connection_id': connection_id,
            '_id': record_id,
            'modified_time': record['modified_time']
        })

        # Check if the record contains a 'snippet' field
        if 'snippet' in record:
            snippets = record_dict.setdefault('snippets', [])
            snippets.append(record['snippet'])

    return search_results


def get_search_result_metadata(search_results, seadb_service, project_uuid, connection_id_type_map):
    """Read only lightweight fields available in each source's schema."""
    for connection_id, record_id_record_map in search_results.items():
        connection_type = connection_id_type_map[connection_id]
        table = EXTRA_SOURCE_TABLE_MAP.get(connection_type) or SCHEMA_TABLE_CONNECTION.get(connection_type)
        fields = [name for name in SEARCH_METADATA_FIELDS if name in table.column]
        if not fields:
            continue

        records = seadb_service.get_records_by_pks(
            base_id=project_uuid,
            table_name=get_connection_table_name(connection_type, connection_id),
            pks=record_id_record_map.keys(),
            fields_str=', '.join(fields),
        )
        for record_id, record in record_id_record_map.items():
            metadata = records.get(record_id) or {}
            for name in fields:
                if metadata.get(name) is not None:
                    record[name] = metadata[name]

    return search_results

def get_title_and_ai_summary_of_search_results(search_results, seadb_service, project_uuid, connection_id_type_map):
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
        records_table_name = get_connection_table_name(connection_type, connection_id)

        # query title and ai_summary for current connection (including tk, kb)
        records_in_seadb = seadb_service.get_records_by_pks(
            base_id=project_uuid,
            table_name=records_table_name,
            pks=record_id_record_map.keys(),
            fields_str='title, ai_summary'
        )

        for record_id in list(record_id_record_map.keys()): # use list() to copy keys for avoiding iter error
            # skip the deleted records
            if record_id not in records_in_seadb:
                del record_id_record_map[record_id]
                continue

            record_id_record_map[record_id]['title'] = records_in_seadb[record_id]['title']
            record_id_record_map[record_id]['ai_summary'] = records_in_seadb[record_id]['ai_summary']
    
    # skip the empty connection records
    for connection_id in list(search_results.keys()):
        if not search_results[connection_id]:
            del search_results[connection_id]

    return search_results
