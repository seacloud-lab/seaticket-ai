import logging
from seaqa_ai.config import MAX_DOCUMENT_CONTENT_SIZE, \
    S3_HOST, S3_KEY_ID, S3_SECRET_KEY, S3_WEB_CRAWL_BUCKET
from seaqa_ai.utils import url_to_filename
from seaqa_ai.utils.constants import ConnectionType, ExtraSourceType
from seaqa_ai.utils.storage import S3Storage
from seaqa_ai.utils.seadb_api import gen_seadb_table_name
from seaqa_ai.managers.ai_utils.utils.search import remap_comments_by_record_id

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
            # get corresponding handler
            method_name = f'_get_{connection_id_type_map[connection_id]}_content'
            method = getattr(self, method_name)
            result = method(record_id_record_map, connection_id)
            record_id_record_map.update(result)
        return search_results

    # documents
    ## knowledge base
    def _get_knowledge_base_content(self, record_id_record_map, _):
        results_from_seadb = self.seadb_api.get_records_by_pks(
            base_id=self.project_uuid,
            table_name=gen_seadb_table_name(ExtraSourceType.KNOWLEDGE_BASE.value)[0],
            pks=record_id_record_map.keys(),
            fields_str='content'
        )
        for record_id, record in record_id_record_map.items():
            # set content from keywords search result (priority) or from seadb
            record['content'] = (record.get('content') or results_from_seadb[record_id]['content'])[:MAX_DOCUMENT_CONTENT_SIZE]
        
        return record_id_record_map

    ## site
    def _get_site_content(self, record_id_record_map, connection_id):
        results_from_seadb = self.seadb_api.get_records_by_pks(
            base_id=self.project_uuid,
            table_name=gen_seadb_table_name(ConnectionType.SITE.value, connection_id)[0],
            pks=record_id_record_map.keys(),
            fields_str='url'
        )
        # set content from keywords search result (priority) or from s3
        with S3Storage(S3_HOST, S3_KEY_ID, S3_SECRET_KEY, S3_WEB_CRAWL_BUCKET) as s3_storage:
            for record_id, record in record_id_record_map.items():
                url = results_from_seadb[record_id]['url']
                record['url'] = url
                record['content'] = (record.get('content') or s3_storage.read_json(
                    site_id=connection_id,
                    project_uuid=self.project_uuid,
                    filename=url_to_filename(url)
                )['content'])[:MAX_DOCUMENT_CONTENT_SIZE]
        return record_id_record_map

    ## seafile
    def _get_seafile_content(self, record_id_record_map, connection_id):
        results_from_seadb = self.seadb_api.get_records_by_pks(
            base_id=self.project_uuid,
            table_name=gen_seadb_table_name(ConnectionType.SEAFILE.value, connection_id)[0],
            pks=record_id_record_map.keys(),
            fields_str='content'
        )

        # set content from keywords search result (priority) or from seadb
        for record_id, record in record_id_record_map.items():
            record['content'] = (record.get('content') or results_from_seadb[record_id]['content'])[:MAX_DOCUMENT_CONTENT_SIZE]
        
        return record_id_record_map

    # issues
    ## ticket
    def _get_ticket_content(self, record_id_record_map, _):
        topics_table_name, comments_table_name = gen_seadb_table_name(ExtraSourceType.TICKET.value)

        # query topics
        record_ids = record_id_record_map.keys()
        record_id_to_topics = self.seadb_api.get_records_by_pks(
            base_id=self.project_uuid,
            table_name=topics_table_name,
            pks=record_ids,
            fields_str='creator, assignees, content, state, substate, type, tags, participants, priority'
        )

        # query comments/replies
        # for ticket topic_id_to_comments also present as record_id_to_comments due to same topic id field
        record_id_to_comments = self.seadb_api.get_comments_by_topic_ids(
            base_id=self.project_uuid,
            table_name=comments_table_name,
            topic_ids=record_ids,
            topic_id_field_name='ticket_id',
            fields_str='creator, content'
        )

        # get all the usernames need to be resolved
        related_usernames = []
        for record_id in record_id_record_map:
            record = record_id_to_topics[record_id]
            comments = record_id_to_comments.get(record_id, [])

            related_usernames.append(record['creator'])
            related_usernames += record['assignees'] or []
            related_usernames += record['participants'] or []
            related_usernames += [
                comment['creator']
                for comment in comments
            ]
        
        # resolved username to nickname
        username_nickname_map = self.username_resolver.emails2nicknames(list(set(related_usernames)), return_map=True)

        # set content from topic and comments
        for record_id, record in record_id_record_map.items():
            record_from_seadb = record_id_to_topics[record_id]
            comments = record_id_to_comments.get(record_id, [])

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
        topics_table_name, comments_table_name = gen_seadb_table_name(ConnectionType.GITHUB_ISSUE.value, connection_id)

        # query topics
        record_id_to_topics = self.seadb_api.get_records_by_pks(
            base_id=self.project_uuid,
            table_name=topics_table_name,
            pks=record_id_record_map.keys(),
            fields_str='issue_id, content, issue_number, state, state_reason, labels, issue_type, author, assignees, url'
        )

        # query comments/replies, the result of the following steps is record_id_to_comments
        issue_id_to_record_id = {
            record['issue_id']: record_id
            for record_id, record in record_id_to_topics.items()
        }
        issue_id_to_comments = self.seadb_api.get_comments_by_topic_ids(
            base_id=self.project_uuid,
            table_name=comments_table_name,
            topic_ids=issue_id_to_record_id.keys(),
            topic_id_field_name='issue_id',
            fields_str='author, content',
            has_deleted_field=False
        )

        record_id_to_comments = remap_comments_by_record_id(issue_id_to_record_id, issue_id_to_comments)

        # set content from topic and comments
        for record_id, record in record_id_record_map.items():
            record_from_seadb = record_id_to_topics[record_id]
            comments = record_id_to_comments.get(record_id, [])

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
            record['url'] = record_from_seadb['url']
        return record_id_record_map

    ## discourse_forum
    def _get_discourse_forum_content(self, record_id_record_map, connection_id):
        topics_table_name, comments_table_name = gen_seadb_table_name(ConnectionType.DISCOURSE_FORUM.value, connection_id)

        # query topics
        record_id_to_topics = self.seadb_api.get_records_by_pks(
            base_id=self.project_uuid,
            table_name=topics_table_name,
            pks=record_id_record_map.keys(),
            fields_str='topic_id, slug, resolved'
        )

        # query comments/replies, the result of the following steps is record_id_to_comments
        topic_id_to_record_id = {
            record['topic_id']: record_id
            for record_id, record in record_id_to_topics.items()
        }
        topic_id_to_comments = self.seadb_api.get_comments_by_topic_ids(
            base_id=self.project_uuid,
            table_name=comments_table_name,
            topic_ids=topic_id_to_record_id.keys(),
            topic_id_field_name='topic_id',
            fields_str='author, post_number, content',
            has_deleted_field=False
        )

        record_id_to_comments = remap_comments_by_record_id(topic_id_to_record_id, topic_id_to_comments)

        # set content from topic and comments
        for record_id, record in record_id_record_map.items():
            record_from_seadb = record_id_to_topics[record_id]
            comments = record_id_to_comments.get(record_id, [])

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
        threads_table_name, replies_table_name = gen_seadb_table_name(ConnectionType.EMAIL.value, connection_id)

        # query threads
        record_ids = record_id_record_map.keys()
        record_id_to_threads = self.seadb_api.get_records_by_pks(
            base_id=self.project_uuid,
            table_name=threads_table_name,
            pks=record_ids,
            fields_str='tags'
        )

        # query emails
        # for email thread_id_to_comments also present as record_id_to_emails due to same topic id field
        record_id_to_emails = self.seadb_api.get_comments_by_topic_ids(
            base_id=self.project_uuid,
            table_name=replies_table_name,
            topic_ids=record_ids,
            topic_id_field_name='thread_id',
            fields_str='title, email_from, email_to, cc, content, html_content, attachments'
        )

        # set content from topic and comments
        for record_id, record in record_id_record_map.items():
            record_from_seadb = record_id_to_threads[record_id]
            emails = record_id_to_emails.get(record_id, [])

            content = {
                'topic_tags': record_from_seadb['tags'],
                'emails': [
                    {
                        'title': email['title'],
                        'email_from': email['email_from'],
                        'email_to': email['email_to'],
                        'cc': email['cc'],
                        'content': email['content'],
                        'html_content': email['html_content']
                    }
                    for email in emails
                ]
            }

            record['content'] = content
        return record_id_record_map
