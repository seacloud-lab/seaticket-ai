import logging
from datetime import datetime, timezone

from seaqa_ai.utils import gen_record_hash
from seaqa_ai.utils.constants import ExtraSourceType
from seaqa_ai.seadb_models.utils import get_connection_table_name

logger = logging.getLogger(__name__)


def _parse_utc_datetime(value):
    if value is None:
        return None
    try:
        dt = datetime.fromisoformat(str(value).replace('Z', '+00:00'))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except (ValueError, TypeError):
        return None


def _is_within_time_window(modified_time, time_from, time_to):
    """Return True if modified_time is usable under the given window.

    When a time window is active, missing/invalid timestamps are excluded.
    When no window is set, all records are kept.
    """
    if time_from is None and time_to is None:
        return True

    modified_dt = _parse_utc_datetime(modified_time)
    if modified_dt is None:
        return False

    if time_from is not None:
        time_from_dt = _parse_utc_datetime(time_from)
        if time_from_dt is not None and modified_dt < time_from_dt:
            return False
    if time_to is not None:
        time_to_dt = _parse_utc_datetime(time_to)
        if time_to_dt is not None and modified_dt > time_to_dt:
            return False
    return True


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
        if ExtraSourceType.is_valid(record['type']):
            connection_id = record['type']

        try:
            record_id = int(record['_id'])
        except:
            record_id = record['_id']

        # Ignore if the record in skip record list
        record_hex = gen_record_hash(record['type'], connection_id, record_id)
        if record_hex in skip_search_record_hashes:
            continue

        modified_time = record.get('modified_time')

        # Retrieve or create a dictionary for the given connection_id from search_results
        connection_dict = search_results.setdefault(connection_id, {})

        # Retrieve or create a dictionary for the search record
        record_dict = connection_dict.setdefault(record_id, {
            'type': record['type'],
            'connection_id': connection_id,
            '_id': record_id,
            'modified_time': modified_time
        })

        # Check if the record contains a 'content' field
        if 'content' in record:
            record_dict['content'] = record['content']

        # Check if the record contains a 'snippet' field
        if 'snippet' in record:
            snippets = record_dict.setdefault('snippets', [])
            snippets.append(record['snippet'])

    return search_results

def remap_comments_by_record_id(topic_id_to_record_id, topic_id_to_comments):
    """
    remap the topic_id - comments map to record_id - comments map
    
    Args:
        topic_id_to_record_id: dict, topic_id -> record_id 的映射
        topic_id_to_comments: dict, topic_id -> comments_list 的映射
    
    Returns:
        dict, record_id -> comments_list 的映射
    """
    return {
        topic_id_to_record_id[topic_id]: comments
        for topic_id, comments in topic_id_to_comments.items()
        if topic_id in topic_id_to_record_id
    }

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
        records_table_name = get_connection_table_name(connection_type, connection_id)

        # query title and ai_summary for current connection (including tk, kb)
        records_in_seadb = seadb_api.get_records_by_pks(
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
