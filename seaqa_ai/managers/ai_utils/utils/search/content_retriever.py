import logging
from seaqa_ai.config import MAX_DOCUMENT_CONTENT_SIZE, \
    S3_HOST, S3_KEY_ID, S3_SECRET_KEY, S3_WEB_CRAWL_BUCKET
from seaqa_ai.utils import url_to_filename
from seaqa_ai.utils.constants import ConnectionType, ExtraSourceType
from seaqa_ai.utils.storage import S3Storage
from seaqa_ai.utils.seadb_api import gen_seadb_table_name

logger = logging.getLogger(__name__)

class ContentRetriever:
    def __init__(self, username_resolver, seadb_api, project_uuid):
        self.username_resolver = username_resolver
        self.seadb_api = seadb_api
        self.project_uuid = project_uuid

    def retrieve(self, search_results, connection_id_type_map):
        """
        func type: retrieve the search results contents from s3/seadb

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
            method_name = f'_get_{connection_id_type_map[connection_id]}_content'
            method = getattr(self, method_name)
            result = method(record_id_record_map, connection_id)
            record_id_record_map.update(result)
        return search_results

    # documents
    ## knowledge base
    def _get_site_content(self, record_id_record_map, _):
        results_from_seadb = self.seadb_api.get_record_and_comments(
            self.project_uuid,
            record_id_record_map.keys(),
            gen_seadb_table_name(ExtraSourceType.KNOWLEDGE_BASE.value)[0],
            record_fields_str='content'
        )
        for record_id, record in record_id_record_map.items():
            record['content'] = (record.get('content') or results_from_seadb[record_id]['record']['content'])[:MAX_DOCUMENT_CONTENT_SIZE]
        
        return record_id_record_map

    ## site
    def _get_site_content(self, record_id_record_map, connection_id):
        results_from_seadb = self.seadb_api.get_record_and_comments(
            self.project_uuid,
            record_id_record_map.keys(),
            gen_seadb_table_name(ConnectionType.SITE.value, connection_id)[0],
            record_fields_str='url'
        )
        with S3Storage(S3_HOST, S3_KEY_ID, S3_SECRET_KEY, S3_WEB_CRAWL_BUCKET) as s3_storage:
            for record_id, record in record_id_record_map.items():
                url = results_from_seadb[record_id]['record']['url']
                record['url'] = url
                record['content'] = (record.get('content') or s3_storage.read_json(
                    site_id=connection_id,
                    project_uuid=self.project_uuid,
                    filename=url_to_filename(url)
                )['content'])[:MAX_DOCUMENT_CONTENT_SIZE]
        return record_id_record_map

    ## seafile
    def _get_seafile_content(self, record_id_record_map, connection_id):
        results_from_seadb = self.seadb_api.get_record_and_comments(
            self.project_uuid,
            record_id_record_map.keys(),
            gen_seadb_table_name(ConnectionType.SEAFILE.value, connection_id)[0],
            record_fields_str='content'
        )
        for record_id, record in record_id_record_map.items():
            record['content'] = (record.get('content') or results_from_seadb[record_id]['record']['content'])[:MAX_DOCUMENT_CONTENT_SIZE]
        
        return record_id_record_map

    # issues
    ## ticket
    def _get_ticket_content(self, record_id_record_map, _):
        connection_table_name, comments_table_name = gen_seadb_table_name(ExtraSourceType.TICKET.value)
        results_from_seadb = self.seadb_api.get_record_and_comments(
            self.project_uuid,
            record_id_record_map.keys(),
            connection_table_name,
            comments_table_name=comments_table_name,
            record_fields_str='creator, assignees, content, state, substate, type, tags, participants, priority',
            comments_field_str='creator, content',
            topic_id_field_name='_pk',
            comments_topic_id_field_name='ticket_id'
        )

        related_usernames = []
        for record_id in record_id_record_map:
            record = results_from_seadb[record_id]['record']
            comments = results_from_seadb[record_id].get('comments', [])

            related_usernames.append(record['creator'])
            related_usernames += record['assignees'] or []
            related_usernames += record['participants'] or []
            related_usernames += [
                comment['creator']
                for comment in comments
            ]
        
        username_nickname_map = self.username_resolver.emails2nicknames(list(set(related_usernames)), return_map=True)

        for record_id, record in record_id_record_map.items():
            record_from_seadb = results_from_seadb[record_id]['record']
            comments = results_from_seadb[record_id].get('comments', [])

            content = {
                'author': username_nickname_map.get(record_from_seadb['creator'], ''),
                'assignees': [
                    username_nickname_map.get(assignee, '')
                    for assignee in record_from_seadb['assignees'] or []
                ],
                'participants': [
                    username_nickname_map.get(participant, '')
                    for participant in record_from_seadb['participants'] or []
                ],
                'content': record_from_seadb['content'],
                'comments': [
                    {
                        'creator': username_nickname_map.get(comment['creator'], ''),
                        'content': comment['content']
                    }
                    for comment in comments
                ],
                'state': record_from_seadb['state'],
                'substate': record_from_seadb['substate'],
                'type': record_from_seadb['type'],
                'tags': record_from_seadb['tags'],
                'priority': record_from_seadb['priority']
            }

            record['content'] = content
        return record_id_record_map
    
    ## github_issue
    def _get_github_issue_content(self, record_id_record_map, connection_id):
        connection_table_name, comments_table_name = gen_seadb_table_name(ConnectionType.GITHUB_ISSUE.value, connection_id)
        results_from_seadb = self.seadb_api.get_record_and_comments(
            self.project_uuid,
            record_id_record_map.keys(),
            connection_table_name,
            comments_table_name=comments_table_name,
            topic_id_field_name='issue_id',
            record_fields_str='issue_id, content, issue_number, state, state_reason, labels, issue_type, author, assignees, url',
            comments_field_str='author, content'
        )

        for record_id, record in record_id_record_map.items():
            record_from_seadb = results_from_seadb[record_id]['record']
            comments = results_from_seadb[record_id].get('comments', [])

            content = {
                'issue_id': record_from_seadb['issue_id'],
                'issue_number': record_from_seadb['issue_number'],
                'state': record_from_seadb['state'],
                'state_reason': record_from_seadb['state_reason'],
                'labels': record_from_seadb['labels'],
                'issue_type': record_from_seadb['issue_type'],
                'author': record_from_seadb['author'],
                'assignees': record_from_seadb['assignees'],
                'content': record_from_seadb['content'],
                'comments': [
                    {
                        'author': comment['author'],
                        'content': comment['content']
                    }
                    for comment in comments
                ]
            }
            record['content'] = content
            record['url'] = record['url']
        return record_id_record_map

    ## discourse_forum
    def _get_discourse_forum_content(self, record_id_record_map, connection_id):
        connection_table_name, comments_table_name = gen_seadb_table_name(ConnectionType.DISCOURSE_FORUM.value, connection_id)

        results_from_seadb = self.seadb_api.get_record_and_comments(
            self.project_uuid,
            record_id_record_map.keys(),
            connection_table_name,
            comments_table_name=comments_table_name,
            topic_id_field_name='topic_id',
            record_fields_str='topic_id, slug, resolved',
            comments_field_str='author, post_number, content'
        )

        for record_id, record in record_id_record_map.items():
            record_from_seadb = results_from_seadb[record_id]['record']
            comments = results_from_seadb[record_id].get('comments', [])

            content = {
                'topic_id': record_from_seadb['topic_id'],
                'slug': record_from_seadb['slug'],
                'resolved': record_from_seadb['resolved'],
                'comments': [
                    {
                        'author': comment['author'],
                        'post_number': comment['post_number'],
                        'content': comment['content']
                    }
                    for comment in comments
                ]
            }
            record['content'] = content
        return record_id_record_map

    ## email
    def _get_email_content(self, record_id_record_map, connection_id):
        connection_table_name, comments_table_name = gen_seadb_table_name(ConnectionType.EMAIL.value, connection_id)
        results_from_seadb = self.seadb_api.get_record_and_comments(
            self.project_uuid,
            record_id_record_map.keys(),
            connection_table_name,
            comments_table_name=comments_table_name,
            topic_id_field_name='_pk',
            record_fields_str='tags',
            comments_field_str='title, email_from, email_to, cc, content, html_content, attachments', comments_topic_id_field_name='thread_id',
            has_deleted_field=True
        )

        for record_id, record in record_id_record_map.items():
            record_from_seadb = results_from_seadb[record_id]['record']
            comments = results_from_seadb[record_id].get('comments', [])

            content = {
                'topic_tags': record_from_seadb['tags'],
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

            record['content'] = content
        return record_id_record_map
