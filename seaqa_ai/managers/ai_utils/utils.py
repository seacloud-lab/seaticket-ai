import logging
from copy import deepcopy
from seaqa_ai.config import MAX_DOCUMENT_CONTENT_SIZE, \
    S3_HOST, S3_KEY_ID, S3_SECRET_KEY, S3_WEB_CRAWL_BUCKET
from seaqa_ai.utils import url_to_filename, gen_attachment_hex
from seaqa_ai.utils.constants import ConnectionType, ExtraSourceType
from seaqa_ai.utils.storage import S3Storage
from seaqa_ai.utils.seadb_api import gen_seadb_table_name

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

def retrieve_content_of_search_results(search_results, seadb_api, project_uuid, connection_id_type_map):
    """
    func type: output formatter

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
    """
    for connection_id, record_id_record_map in search_results.items():
        connection_type = connection_id_type_map[connection_id]
        connection_table_name, comments_table_name = gen_seadb_table_name(connection_type, connection_id)
        # documents
        if connection_type == ExtraSourceType.KNOWLEDGE_BASE.value:
            seadb_record_id_record_comments_map = seadb_api.get_record_and_comments(project_uuid, record_id_record_map.keys(), connection_table_name, record_fields_str='content')
            for record_id, record in record_id_record_map.items():
                search_results[connection_id][record_id]['content'] = seadb_record_id_record_comments_map[record_id]['record']['content'][:MAX_DOCUMENT_CONTENT_SIZE]
        elif connection_type == ConnectionType.SITE.value:
            with S3Storage(S3_HOST, S3_KEY_ID, S3_SECRET_KEY, S3_WEB_CRAWL_BUCKET) as s3_storage:
                project_uuid_to_s3 = project_uuid.replace('-', '')
                for record_id, record in record_id_record_map.items():
                    search_results[connection_id][record_id]['content'] = s3_storage.read_json(
                        site_id=connection_id,
                        project_uuid=project_uuid_to_s3,
                        filename=url_to_filename(record['url'])
                    )['content'][:MAX_DOCUMENT_CONTENT_SIZE]

        elif connection_type == ConnectionType.SEAFILE.value:
            seadb_record_id_record_comments_map = seadb_api.get_record_and_comments(project_uuid, record_id_record_map.keys(), connection_table_name, record_fields_str='content')
            for record_id, record in record_id_record_map.items():
                search_results[connection_id][record_id]['content'] = seadb_record_id_record_comments_map[record_id]['record']['content'][:MAX_DOCUMENT_CONTENT_SIZE]

        # issues
        elif connection_type == ExtraSourceType.TICKET.value:
            seadb_record_id_record_comments_map = seadb_api.get_record_and_comments(project_uuid, record_id_record_map.keys(), connection_table_name, comments_table_name=comments_table_name, record_fields_str='content', comments_field_str='creator, content', topic_id_field_name='_pk', comments_topic_id_field_name='ticket_id', has_deleted_field=True)
            for record_id, record in record_id_record_map.items():
                search_results[connection_id][record_id]['content'] = seadb_record_id_record_comments_map[record_id]['record']['content']
                search_results[connection_id][record_id]['comments'] = [
                    {
                        'creator': comment['creator'],
                        'content': comment['content']
                    }
                    for comment in seadb_record_id_record_comments_map[record_id].get('comments', [])
                ]
                search_results[connection_id][record_id]['content'] = deepcopy(search_results[connection_id][record_id])
                del search_results[connection_id][record_id]['comments']
        elif connection_type == ConnectionType.DISCOURSE_FORUM.value:
            seadb_record_id_record_comments_map = seadb_api.get_record_and_comments(project_uuid, record_id_record_map.keys(), connection_table_name, comments_table_name=comments_table_name, topic_id_field_name='topic_id', record_fields_str='_pk', comments_field_str='author, post_number, content')
            for record_id, record in record_id_record_map.items():
                search_results[connection_id][record_id]['comments'] = [
                    {
                        'author': comment['author'],
                        'post_number': comment['post_number'],
                        'content': comment['content']
                    }
                    for comment in seadb_record_id_record_comments_map[record_id].get('comments', [])
                ]
                search_results[connection_id][record_id]['content'] = deepcopy(search_results[connection_id][record_id])
                del search_results[connection_id][record_id]['comments']
        elif connection_type == ConnectionType.GITHUB_ISSUE.value:
            seadb_record_id_record_comments_map = seadb_api.get_record_and_comments(project_uuid, record_id_record_map.keys(), connection_table_name, comments_table_name=comments_table_name, topic_id_field_name='issue_id', record_fields_str='content', comments_field_str='author, content')
            for record_id, record in record_id_record_map.items():
                search_results[connection_id][record_id]['content'] = seadb_record_id_record_comments_map[record_id]['record']['content']
                search_results[connection_id][record_id]['comments'] = [
                    {
                        'author': comment['author'],
                        'content': comment['content']
                    }
                    for comment in seadb_record_id_record_comments_map[record_id].get('comments', [])
                ]
                search_results[connection_id][record_id]['content'] = deepcopy(search_results[connection_id][record_id])
                del search_results[connection_id][record_id]['comments']
        elif connection_type == ConnectionType.EMAIL.value:
            seadb_record_id_record_comments_map = seadb_api.get_record_and_comments(project_uuid, record_id_record_map.keys(), connection_table_name, record_fields_str='content')
            for record_id, record in record_id_record_map.items():
                search_results[connection_id][record_id]['content'] = seadb_record_id_record_comments_map[record_id]['record']['content']

    return search_results
