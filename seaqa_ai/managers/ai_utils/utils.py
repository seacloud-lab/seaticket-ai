import logging
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

def retrieve_content_of_search_results(username_resolver, search_results, seadb_api, project_uuid, connection_id_type_map, special_content_model=False):
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

    The notes of this function see KNOWLEDGE_BASE as example
    """

    for connection_id, record_id_record_map in search_results.items():
        connection_type = connection_id_type_map[connection_id]
        connection_table_name, comments_table_name = gen_seadb_table_name(connection_type, connection_id)
        # documents
        if not special_content_model and connection_type == ExtraSourceType.KNOWLEDGE_BASE.value:
            seadb_record_id_record_comments_map = seadb_api.get_record_and_comments(project_uuid, record_id_record_map.keys(), connection_table_name, record_fields_str='title, ai_summary, content')
            for record_id in list(record_id_record_map.keys()): # use list() to copy keys for avoiding iter error
                if record_id not in seadb_record_id_record_comments_map:
                    del record_id_record_map[record_id] # --> deleted record, do not sent to AI
                    continue

                record_id_record_map[record_id]['title'] = seadb_record_id_record_comments_map[record_id]['record']['title']
                record_id_record_map[record_id]['ai_summary'] = seadb_record_id_record_comments_map[record_id]['record']['ai_summary']
                record_id_record_map[record_id]['content'] = seadb_record_id_record_comments_map[record_id]['record']['content'][:MAX_DOCUMENT_CONTENT_SIZE] # --> to AI
        elif connection_type == ConnectionType.SITE.value:
            seadb_record_id_record_comments_map = seadb_api.get_record_and_comments(project_uuid, record_id_record_map.keys(), connection_table_name, record_fields_str='title, ai_summary, url')
            if special_content_model:
                with S3Storage(S3_HOST, S3_KEY_ID, S3_SECRET_KEY, S3_WEB_CRAWL_BUCKET) as s3_storage:
                    project_uuid_to_s3 = project_uuid.replace('-', '')
                    for record_id in list(record_id_record_map.keys()):
                        url = seadb_record_id_record_comments_map[record_id]['record']['url']
                        record_id_record_map[record_id]['url'] = url
                        record_id_record_map[record_id]['content'] = s3_storage.read_json(
                            site_id=connection_id,
                            project_uuid=project_uuid_to_s3,
                            filename=url_to_filename(url)
                        )['content'][:MAX_DOCUMENT_CONTENT_SIZE]
            else:
                for record_id in list(record_id_record_map.keys()):
                    if record_id not in seadb_record_id_record_comments_map:
                        del record_id_record_map[record_id]
                        continue

                    record_id_record_map[record_id]['title'] = seadb_record_id_record_comments_map[record_id]['record']['title']
                    record_id_record_map[record_id]['ai_summary'] = seadb_record_id_record_comments_map[record_id]['record']['ai_summary']

        elif not special_content_model and connection_type == ConnectionType.SEAFILE.value:
            seadb_record_id_record_comments_map = seadb_api.get_record_and_comments(project_uuid, record_id_record_map.keys(), connection_table_name, record_fields_str='title, ai_summary, content')
            for record_id in list(record_id_record_map.keys()):
                if record_id not in seadb_record_id_record_comments_map:
                    del record_id_record_map[record_id]
                    continue

                record_id_record_map[record_id]['title'] = seadb_record_id_record_comments_map[record_id]['record']['title']
                record_id_record_map[record_id]['ai_summary'] = seadb_record_id_record_comments_map[record_id]['record']['ai_summary']
                record_id_record_map[record_id]['content'] = seadb_record_id_record_comments_map[record_id]['record']['content'][:MAX_DOCUMENT_CONTENT_SIZE]

        # issues
        elif connection_type == ExtraSourceType.TICKET.value:
            if not special_content_model:
                comments_table_name=None

            seadb_record_id_record_comments_map = seadb_api.get_record_and_comments(project_uuid, record_id_record_map.keys(), connection_table_name, comments_table_name=comments_table_name, record_fields_str='title, ai_summary, creator, assignees, content, state, substate, type, tags, participants, priority', comments_field_str='creator, content', topic_id_field_name='_pk', comments_topic_id_field_name='ticket_id')

            if special_content_model:
                related_usernames = []
                for record_id in list(record_id_record_map.keys()):
                    record = seadb_record_id_record_comments_map[record_id]['record']
                    comments = seadb_record_id_record_comments_map[record_id].get('comments', [])

                    related_usernames.append(record['creator'])
                    related_usernames += record['assignees'] or []
                    related_usernames += record['participants'] or []
                    related_usernames += [
                        comment['creator']
                        for comment in comments
                    ]
                
                username_nickname_map = username_resolver.emails2nicknames(list(set(related_usernames)), return_map=True)

                for record_id in list(record_id_record_map.keys()):
                    record = seadb_record_id_record_comments_map[record_id]['record']
                    comments = seadb_record_id_record_comments_map[record_id].get('comments', [])

                    content = {
                        'author': username_nickname_map.get(record['creator'], ''),
                        'assignees': [
                            username_nickname_map.get(assignee, '')
                            for assignee in record['assignees'] or []
                        ],
                        'participants': [
                            username_nickname_map.get(participant, '')
                            for participant in record['participants'] or []
                        ],
                        'content': record['content'],
                        'comments': [
                            {
                                'creator': username_nickname_map.get(comment['creator'], ''),
                                'content': comment['content']
                            }
                            for comment in comments
                        ],
                        'state': record['state'],
                        'substate': record['substate'],
                        'type': record['type'],
                        'tags': record['tags'],
                        'priority': record['priority']
                    }

                    record_id_record_map[record_id]['content'] = content
            else:
                for record_id in list(record_id_record_map.keys()):
                    if record_id not in seadb_record_id_record_comments_map:
                        del record_id_record_map[record_id]
                        continue

                    record_id_record_map[record_id]['title'] = seadb_record_id_record_comments_map[record_id]['record']['title']
                    record_id_record_map[record_id]['ai_summary'] = seadb_record_id_record_comments_map[record_id]['record']['ai_summary']

        elif connection_type == ConnectionType.DISCOURSE_FORUM.value:
            if not special_content_model:
                comments_table_name=None

            seadb_record_id_record_comments_map = seadb_api.get_record_and_comments(project_uuid, record_id_record_map.keys(), connection_table_name, comments_table_name=comments_table_name, topic_id_field_name='topic_id', record_fields_str='title, ai_summary, topic_id, slug, resolved', comments_field_str='author, post_number, content')

            if special_content_model:
                for record_id in list(record_id_record_map.keys()):
                    record = seadb_record_id_record_comments_map[record_id]['record']
                    comments = seadb_record_id_record_comments_map[record_id].get('comments', [])

                    content = {
                        'topic_id': record['topic_id'],
                        'slug': record['slug'],
                        'resolved': record['resolved'],
                        'comments': [
                            {
                                'author': comment['author'],
                                'post_number': comment['post_number'],
                                'content': comment['content']
                            }
                            for comment in seadb_record_id_record_comments_map[record_id].get('comments', [])
                        ]
                    }
                    record_id_record_map[record_id]['content'] = content
            else:
                for record_id in list(record_id_record_map.keys()):
                    if record_id not in seadb_record_id_record_comments_map:
                        del record_id_record_map[record_id]
                        continue

                    record_id_record_map[record_id]['title'] = seadb_record_id_record_comments_map[record_id]['record']['title']
                    record_id_record_map[record_id]['ai_summary'] = seadb_record_id_record_comments_map[record_id]['record']['ai_summary']

        elif connection_type == ConnectionType.GITHUB_ISSUE.value:
            if not special_content_model:
                comments_table_name=None

            seadb_record_id_record_comments_map = seadb_api.get_record_and_comments(project_uuid, record_id_record_map.keys(), connection_table_name, comments_table_name=comments_table_name, topic_id_field_name='issue_id', record_fields_str='title, ai_summary, issue_id, content, issue_number, state, state_reason, labels, issue_type, author, assignees, url', comments_field_str='author, content')

            if special_content_model:
                for record_id in list(record_id_record_map.keys()):
                    record = seadb_record_id_record_comments_map[record_id]['record']
                    comments = seadb_record_id_record_comments_map[record_id].get('comments', [])

                    content = {
                        'issue_id': record['issue_id'],
                        'issue_number': record['issue_number'],
                        'state': record['state'],
                        'state_reason': record['state_reason'],
                        'labels': record['labels'],
                        'issue_type': record['issue_type'],
                        'author': record['author'],
                        'assignees': record['assignees'],
                        'content': record['content'],
                        'comments': [
                            {
                                'author': comment['author'],
                                'content': comment['content']
                            }
                            for comment in comments
                        ]
                    }
                    record_id_record_map[record_id]['content'] = content
                    record_id_record_map[record_id]['url'] = record['url']
            else:
                for record_id in list(record_id_record_map.keys()):
                    if record_id not in seadb_record_id_record_comments_map:
                        del record_id_record_map[record_id]
                        continue

                    record_id_record_map[record_id]['title'] = seadb_record_id_record_comments_map[record_id]['record']['title']
                    record_id_record_map[record_id]['ai_summary'] = seadb_record_id_record_comments_map[record_id]['record']['ai_summary']
    
        elif connection_type == ConnectionType.EMAIL.value:
            if not special_content_model:
                comments_table_name=None

            seadb_record_id_record_comments_map = seadb_api.get_record_and_comments(project_uuid, record_id_record_map.keys(), connection_table_name, comments_table_name=comments_table_name, topic_id_field_name='_pk', record_fields_str='title, ai_summary, tags', comments_field_str='title, email_from, email_to, cc, content, html_content, attachments', comments_topic_id_field_name='thread_id', has_deleted_field=True)

            if special_content_model:
                for record_id in list(record_id_record_map.keys()):
                    record = seadb_record_id_record_comments_map[record_id]['record']
                    comments = seadb_record_id_record_comments_map[record_id].get('comments', [])

                    content = {
                        'topic_tags': record['tags'],
                        'emails': [
                            {
                                'title': comment['title'],
                                'email_from': comment['email_from'],
                                'email_to': comment['email_to'],
                                'cc': comment['cc'],
                                'content': comment['content'],
                                'html_content': comment['html_content']
                            }
                            for comment in comments
                        ]
                    }

                    record_id_record_map[record_id]['content'] = content
            else:
                for record_id in list(record_id_record_map.keys()):
                    if record_id not in seadb_record_id_record_comments_map:
                        del record_id_record_map[record_id]
                        continue

                    record_id_record_map[record_id]['title'] = seadb_record_id_record_comments_map[record_id]['record']['title']
                    record_id_record_map[record_id]['ai_summary'] = seadb_record_id_record_comments_map[record_id]['record']['ai_summary']

    # remove empty record map
    if not special_content_model:
        for connection_id in list(search_results.keys()):
            if not search_results[connection_id]:
                del search_results[connection_id]

    return search_results
