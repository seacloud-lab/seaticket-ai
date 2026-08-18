import logging
import re
from html import unescape
from seaqa_ai.config import SEARCH_TOOL_CONTENT_MAX_SIZE, SEARCH_TOOL_ISSUES_MAX_COMMENTS, \
    S3_HOST, S3_KEY_ID, S3_SECRET_KEY, S3_WEB_CRAWL_BUCKET
from seaqa_ai.utils import url_to_filename, extract_markdown_image_url, normalize_image_url
from seaqa_ai.utils.storage import S3Storage
from seaqa_ai.seadb_models import SchemaTables
from seaqa_ai.services.ai_services.utils.search import remap_comments_by_record_id

logger = logging.getLogger(__name__)

MARKDOWN_IMAGE_URL_RE = re.compile(r'!\[[^\]]*\]\(([^)\s]+(?:\s+"[^"]*")?)\)')
HTML_IMAGE_URL_RE = re.compile(r'<img[^>]+src=["\']([^"\']+)["\']', re.IGNORECASE)

COMMENTS_OMITTED_NOTICE = '[comments omitted due to content limit]'
MORE_COMMENTS_OMITTED_NOTICE = '[more comments omitted due to content limit]'
MORE_REPLIES_OMITTED_NOTICE = '[more replies omitted due to content limit]'
MORE_EMAILS_OMITTED_NOTICE = '[more emails omitted due to content limit]'


def extract_image_urls(content):
    if not isinstance(content, str) or not content:
        return []

    image_urls = []
    for match in MARKDOWN_IMAGE_URL_RE.findall(content):
        raw_image_url = unescape(extract_markdown_image_url(match))
        image_url = normalize_image_url(raw_image_url)
        if image_url:
            image_urls.append(image_url)

    for match in HTML_IMAGE_URL_RE.findall(content):
        raw_image_url = unescape(match.strip())
        image_url = normalize_image_url(raw_image_url)
        if image_url:
            image_urls.append(image_url)

    deduped_urls = []
    seen = set()
    for image_url in image_urls:
        if image_url in seen:
            continue
        seen.add(image_url)
        deduped_urls.append(image_url)
    return deduped_urls


def merge_image_urls(contents):
    image_urls = []
    seen = set()
    for content in contents:
        for image_url in extract_image_urls(content):
            if image_url in seen:
                continue
            seen.add(image_url)
            image_urls.append(image_url)
    return image_urls


def build_ticket_comment_notice(content):
    return {
        'creator': None,
        'content': content,
    }


def build_author_comment_notice(content):
    return {
        'author': None,
        'content': content,
    }


def build_discourse_reply_notice(content):
    return {
        'author': None,
        'post_number': None,
        'content': content,
    }


def build_email_notice(content):
    return {
        'title': None,
        'email_from': None,
        'email_to': None,
        'cc': None,
        'content': content,
        'message_id': None,
        'modified_time': None,
    }

class ContentRetriever:
    def __init__(self, username_resolver, seadb, project_uuid):
        self.username_resolver = username_resolver
        self.seadb = seadb
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
        results_from_seadb = self.seadb.get_records_by_pks(
            base_id=self.project_uuid,
            table_name=SchemaTables.KNOWLEDGE_BASE.table_name(),
            pks=record_id_record_map.keys(),
            fields_str='content'
        )
        for record_id, record in record_id_record_map.items():
            full_content = results_from_seadb[record_id]['content']
            # set content from keywords search result (priority) or from seadb
            record['content'] = (record.get('content') or full_content)[:SEARCH_TOOL_CONTENT_MAX_SIZE]
            record['images'] = extract_image_urls(full_content)
        
        return record_id_record_map

    ## site
    def _get_site_content(self, record_id_record_map, connection_id):
        results_from_seadb = self.seadb.get_records_by_pks(
            base_id=self.project_uuid,
            table_name=SchemaTables.WEB_CRAWL.table_name(connection_id),
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
                )['content'])[:SEARCH_TOOL_CONTENT_MAX_SIZE]
        return record_id_record_map

    ## seafile
    def _get_seafile_content(self, record_id_record_map, connection_id):
        results_from_seadb = self.seadb.get_records_by_pks(
            base_id=self.project_uuid,
            table_name=SchemaTables.SEAFILE.table_name(connection_id),
            pks=record_id_record_map.keys(),
            fields_str='content'
        )

        # set content from keywords search result (priority) or from seadb
        for record_id, record in record_id_record_map.items():
            record['content'] = (record.get('content') or results_from_seadb[record_id]['content'])[:SEARCH_TOOL_CONTENT_MAX_SIZE]
        
        return record_id_record_map

    # issues
    ## ticket
    def _get_ticket_content(self, record_id_record_map, _):
        topics_table_name = SchemaTables.TICKETS.table_name()
        comments_table_name = SchemaTables.TICKET_COMMENTS.table_name()

        # query topics
        record_ids = record_id_record_map.keys()
        record_id_to_topics = self.seadb.get_records_by_pks(
            base_id=self.project_uuid,
            table_name=topics_table_name,
            pks=record_ids,
            fields_str='creator, assignees, content, state, substate, type, tags, participants, priority'
        )

        initial_content_lengths = {
            record_id: len((record_id_to_topics[record_id]['content'] or ''))
            for record_id in record_ids
            if record_id in record_id_to_topics
        }

        # query comments/replies with first + latest attachment selection semantics
        record_id_to_comments = self.seadb.get_selected_topic_entries(
            base_id=self.project_uuid,
            table_name=comments_table_name,
            topic_ids=record_ids,
            topic_id_field_name='ticket_id',
            fields_str='_pk, creator, content',
            limit_for_each_topic=SEARCH_TOOL_ISSUES_MAX_COMMENTS,
            first_order_by_field='_pk',
            latest_order_by_field='_pk',
            dedupe_key_fields=['_pk'],
            sort_key_fields=['_pk'],
            initial_content_lengths=initial_content_lengths,
            max_total_content_size=SEARCH_TOOL_CONTENT_MAX_SIZE,
            more_omitted_notice_builder=lambda: build_ticket_comment_notice(MORE_COMMENTS_OMITTED_NOTICE),
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
            record_content = record_from_seadb['content'] or ''
            image_source_contents = [record_content]

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
                'content': record_content[:SEARCH_TOOL_CONTENT_MAX_SIZE],
                'comments': [],
                'state': record_from_seadb['state'],
                'substate': record_from_seadb['substate'],
                'type': record_from_seadb['type'],
                'tags': record_from_seadb['tags'],
                'priority': record_from_seadb['priority']
            }

            total_content_size = len(content['content'])
            for comment in comments:
                comment_content = comment['content'] or ''
                image_source_contents.append(comment_content)
                content['comments'].append({
                    'creator': username_nickname_map.get(comment['creator'], ''),
                    'content': comment_content
                })
                total_content_size += len(comment_content)
            record['content'] = content
            record['images'] = merge_image_urls(image_source_contents)
        return record_id_record_map
    
    ## github_issue
    def _get_github_issue_content(self, record_id_record_map, connection_id):
        topics_table_name = SchemaTables.GITHUB_ISSUES.table_name(connection_id)
        comments_table_name = SchemaTables.GITHUB_ISSUE_COMMENTS.table_name(connection_id)

        # query topics
        record_id_to_topics = self.seadb.get_records_by_pks(
            base_id=self.project_uuid,
            table_name=topics_table_name,
            pks=record_id_record_map.keys(),
            fields_str='issue_id, content, issue_number, state, state_reason, labels, issue_type, author, assignees, url, linked_ticket'
        )

        # query comments/replies, the result of the following steps is record_id_to_comments
        issue_id_to_record_id = {
            record['issue_id']: record_id
            for record_id, record in record_id_to_topics.items()
        }
        initial_content_lengths = {
            record['issue_id']: len((record.get('content') or ''))
            for record in record_id_to_topics.values()
        }
        issue_id_to_comments = self.seadb.get_selected_topic_entries(
            base_id=self.project_uuid,
            table_name=comments_table_name,
            topic_ids=issue_id_to_record_id.keys(),
            topic_id_field_name='issue_id',
            fields_str='comment_id, author, content',
            has_deleted_field=False,
            limit_for_each_topic=SEARCH_TOOL_ISSUES_MAX_COMMENTS,
            first_order_by_field='comment_id',
            latest_order_by_field='comment_id',
            dedupe_key_fields=['comment_id'],
            sort_key_fields=['comment_id'],
            initial_content_lengths=initial_content_lengths,
            max_total_content_size=SEARCH_TOOL_CONTENT_MAX_SIZE,
            more_omitted_notice_builder=lambda: build_author_comment_notice(MORE_COMMENTS_OMITTED_NOTICE),
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
                'linked_ticket': record_from_seadb['linked_ticket'],
                'url': record_from_seadb['url'],
                'content': (record_from_seadb['content'] or '')[:SEARCH_TOOL_CONTENT_MAX_SIZE],
                'comments': []
            }

            total_content_size = len(content['content'])
            for comment in comments:
                comment_content = comment['content'] or ''

                content['comments'].append({
                    'author': comment['author'],
                    'content': comment_content
                })
                total_content_size += len(comment_content)
            record['content'] = content
        return record_id_record_map

    ## discourse_forum
    def _get_discourse_forum_content(self, record_id_record_map, connection_id):
        topics_table_name = SchemaTables.DISCOURSE_TOPICS.table_name(connection_id)
        comments_table_name = SchemaTables.DISCOURSE_REPLIES.table_name(connection_id)

        # query topics
        record_id_to_topics = self.seadb.get_records_by_pks(
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
        topic_id_to_comments = self.seadb.get_selected_topic_entries(
            base_id=self.project_uuid,
            table_name=comments_table_name,
            topic_ids=topic_id_to_record_id.keys(),
            topic_id_field_name='topic_id',
            fields_str='author, post_number, content',
            has_deleted_field=False,
            limit_for_each_topic=SEARCH_TOOL_ISSUES_MAX_COMMENTS,
            first_order_by_field='post_number',
            latest_order_by_field='post_number',
            dedupe_key_fields=['post_number'],
            sort_key_fields=['post_number'],
            initial_content_lengths={topic_id: 0 for topic_id in topic_id_to_record_id.keys()},
            max_total_content_size=SEARCH_TOOL_CONTENT_MAX_SIZE,
            more_omitted_notice_builder=lambda: build_discourse_reply_notice(MORE_REPLIES_OMITTED_NOTICE),
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
                'comments': []
            }

            total_content_size = 0
            for comment in comments:
                comment_content = comment['content'] or ''

                content['comments'].append({
                    'author': comment['author'],
                    'post_number': comment['post_number'],
                    'content': comment_content
                })
                total_content_size += len(comment_content)
            record['content'] = content
        return record_id_record_map

    ## email
    def _get_email_content(self, record_id_record_map, connection_id):
        threads_table_name = SchemaTables.THREAD.table_name(connection_id)
        replies_table_name = SchemaTables.EMAIL.table_name(connection_id)

        # query threads
        record_ids = record_id_record_map.keys()
        record_id_to_threads = self.seadb.get_records_by_pks(
            base_id=self.project_uuid,
            table_name=threads_table_name,
            pks=record_ids,
            fields_str='tags'
        )

        # query emails with first + latest attachment selection semantics
        record_id_to_emails = self.seadb.get_selected_topic_entries(
            base_id=self.project_uuid,
            table_name=replies_table_name,
            topic_ids=record_ids,
            topic_id_field_name='thread_id',
            fields_str='title, email_from, email_to, cc, content, message_id, modified_time',
            limit_for_each_topic=SEARCH_TOOL_ISSUES_MAX_COMMENTS,
            first_order_by_field='modified_time',
            latest_order_by_field='modified_time',
            dedupe_key_fields=['message_id', 'modified_time', 'email_from', 'email_to', 'content'],
            sort_key_fields=['modified_time', 'message_id'],
            initial_content_lengths={record_id: 0 for record_id in record_ids},
            max_total_content_size=SEARCH_TOOL_CONTENT_MAX_SIZE,
            more_omitted_notice_builder=lambda: build_email_notice(MORE_EMAILS_OMITTED_NOTICE),
        )

        # set content from topic and comments
        for record_id, record in record_id_record_map.items():
            record_from_seadb = record_id_to_threads[record_id]
            emails = record_id_to_emails.get(record_id, [])

            content = {
                'topic_tags': record_from_seadb['tags'],
                'emails': []
            }

            total_content_size = 0
            for email in emails:
                email_content = email['content'] or ""

                content['emails'].append({
                    'title': email['title'],
                    'email_from': email['email_from'],
                    'email_to': email['email_to'],
                    'cc': email['cc'],
                    'content': email_content
                })
                total_content_size += len(email_content)
            record['content'] = content
        return record_id_record_map


    # tasks
    ## general task
    def _get_general_task_content(self, record_id_record_map, connection_id):
        results_from_seadb = self.seadb.get_records_by_pks(
            base_id=self.project_uuid,
            table_name=SchemaTables.GENERAL_TASK.table_name(connection_id),
            pks=record_id_record_map.keys(),
            fields_str='content'
        )

        # set content from keywords search result (priority) or from seadb
        for record_id, record in record_id_record_map.items():
            record['content'] = (results_from_seadb[record_id]['content'] or '')[:SEARCH_TOOL_CONTENT_MAX_SIZE]
        
        return record_id_record_map

    ## linear
    def _get_linear_content(self, record_id_record_map, connection_id):
        issues_table_name = SchemaTables.LINEAR_ISSUES.table_name(connection_id)
        comments_table_name = SchemaTables.LINEAR_ISSUE_COMMENTS.table_name(connection_id)

        record_id_to_issues = self.seadb.get_records_by_pks(
            base_id=self.project_uuid,
            table_name=issues_table_name,
            pks=record_id_record_map.keys(),
            fields_str='issue_id, identifier, content, state, labels, author, assignees, priority, due_date, linked_ticket'
        )

        issue_id_to_record_id = {
            record['issue_id']: record_id
            for record_id, record in record_id_to_issues.items()
        }
        initial_content_lengths = {
            record['issue_id']: len((record.get('content') or ''))
            for record in record_id_to_issues.values()
        }
        issue_id_to_comments = self.seadb.get_selected_topic_entries(
            base_id=self.project_uuid,
            table_name=comments_table_name,
            topic_ids=issue_id_to_record_id.keys(),
            topic_id_field_name='issue_id',
            fields_str='comment_id, author, content, created_time',
            has_deleted_field=False,
            limit_for_each_topic=SEARCH_TOOL_ISSUES_MAX_COMMENTS,
            first_order_by_field='created_time',
            latest_order_by_field='created_time',
            dedupe_key_fields=['comment_id'],
            sort_key_fields=['created_time'],
            initial_content_lengths=initial_content_lengths,
            max_total_content_size=SEARCH_TOOL_CONTENT_MAX_SIZE,
            more_omitted_notice_builder=lambda: build_author_comment_notice(MORE_COMMENTS_OMITTED_NOTICE),
        )
        record_id_to_comments = remap_comments_by_record_id(issue_id_to_record_id, issue_id_to_comments)

        for record_id, record in record_id_record_map.items():
            record_from_seadb = record_id_to_issues[record_id]
            comments = record_id_to_comments.get(record_id, [])

            content = {
                'issue_id': record_from_seadb['issue_id'],
                'identifier': record_from_seadb['identifier'],
                'state': record_from_seadb['state'],
                'labels': record_from_seadb['labels'],
                'author': record_from_seadb['author'],
                'assignees': record_from_seadb['assignees'],
                'priority': record_from_seadb['priority'],
                'due_date': record_from_seadb['due_date'],
                'linked_ticket': record_from_seadb['linked_ticket'],
                'content': (record_from_seadb['content'] or '')[:SEARCH_TOOL_CONTENT_MAX_SIZE],
                'comments': []
            }

            for comment in comments:
                content['comments'].append({
                    'author': comment['author'],
                    'created_time': comment.get('created_time'),
                    'content': comment['content'] or ''
                })
            record['content'] = content
        return record_id_record_map
