import logging
import re
from html import unescape
from seaqa_ai.config import SEARCH_TOOL_CONTENT_MAX_SIZE, SEARCH_TOOL_ISSUES_MAX_COMMENTS, \
    S3_HOST, S3_KEY_ID, S3_SECRET_KEY, S3_WEB_CRAWL_BUCKET
from seaqa_ai.utils import url_to_filename, extract_markdown_image_url, normalize_image_url
from seaqa_ai.utils.storage import S3Storage
from seaqa_ai.utils.seadb import ContentBudget, ContentPriority, ticket_comments_query, portal_issue_comments_query, \
    github_issue_comments_query, discourse_replies_query, email_messages_query, \
    linear_issue_comments_query
from seaqa_ai.seadb_models import SchemaTables

logger = logging.getLogger(__name__)

MARKDOWN_IMAGE_URL_RE = re.compile(r'!\[[^\]]*\]\(([^)\s]+(?:\s+"[^"]*")?)\)')
HTML_IMAGE_URL_RE = re.compile(r'<img[^>]+src=["\']([^"\']+)["\']', re.IGNORECASE)

MORE_COMMENTS_OMITTED_NOTICE = '[more comments omitted due to content limit]'
MORE_REPLIES_OMITTED_NOTICE = '[more replies omitted due to content limit]'
MORE_EMAILS_OMITTED_NOTICE = '[more emails omitted due to content limit]'

SEARCH_CONTENT_BUDGET = ContentBudget(max_chars=SEARCH_TOOL_CONTENT_MAX_SIZE, priority=ContentPriority.BODY_FIRST)


def chronological_messages(thread_content):
    """History followed by the latest message, i.e. display order."""
    messages = list(thread_content.history_messages)
    if thread_content.latest_message is not None:
        messages.append(thread_content.latest_message)
    return messages


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


class ContentRetriever:
    def __init__(self, username_resolver, seadb_service, project_uuid):
        self.username_resolver = username_resolver
        self.seadb_service = seadb_service
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
        results_from_seadb = self.seadb_service.get_records_by_pks(
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
        results_from_seadb = self.seadb_service.get_records_by_pks(
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
        results_from_seadb = self.seadb_service.get_records_by_pks(
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
        record_id_to_topics = self.seadb_service.get_records_by_pks(
            base_id=self.project_uuid,
            table_name=topics_table_name,
            pks=record_ids,
            fields_str='creator, assignees, content, state, substate, type, tags, participants, priority'
        )

        content_by_ticket_id = self.seadb_service.get_thread_content(
            self.project_uuid,
            record_ids,
            query=ticket_comments_query(comments_table_name, SEARCH_TOOL_ISSUES_MAX_COMMENTS),
            bodies_by_thread_id={
                record_id: record_id_to_topics[record_id]['content']
                for record_id in record_ids
                if record_id in record_id_to_topics
            },
            budget=SEARCH_CONTENT_BUDGET,
        )

        # get all the usernames need to be resolved
        related_usernames = []
        for record_id in record_id_record_map:
            record = record_id_to_topics[record_id]
            thread_content = content_by_ticket_id[record_id]

            related_usernames.append(record['creator'])
            related_usernames += record['assignees'] or []
            related_usernames += record['participants'] or []
            related_usernames += [
                message['creator']
                for message in chronological_messages(thread_content)
            ]

        # resolved username to nickname
        username_nickname_map = self.username_resolver.emails2nicknames(list(set(related_usernames)), return_map=True)

        # set content from topic and comments
        for record_id, record in record_id_record_map.items():
            record_from_seadb = record_id_to_topics[record_id]
            thread_content = content_by_ticket_id[record_id]
            image_source_contents = [record_from_seadb['content'] or '']

            comments = [
                {
                    'creator': username_nickname_map.get(message['creator'], ''),
                    'content': message['content'] or '',
                }
                for message in chronological_messages(thread_content)
            ]
            if thread_content.has_omitted_messages:
                comments.append(build_ticket_comment_notice(MORE_COMMENTS_OMITTED_NOTICE))
            image_source_contents += [comment['content'] for comment in comments]

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
                'content': thread_content.body,
                'comments': comments,
                'state': record_from_seadb['state'],
                'substate': record_from_seadb['substate'],
                'type': record_from_seadb['type'],
                'tags': record_from_seadb['tags'],
                'priority': record_from_seadb['priority']
            }

            record['content'] = content
            record['images'] = merge_image_urls(image_source_contents)
        return record_id_record_map

    def _get_portal_issue_content(self, record_id_record_map, _):
        issues_table_name = SchemaTables.PORTAL_ISSUES.table_name()
        comments_table_name = SchemaTables.PORTAL_ISSUE_COMMENTS.table_name()
        record_ids = record_id_record_map.keys()
        issues_by_id = self.seadb_service.get_records_by_pks(
            base_id=self.project_uuid,
            table_name=issues_table_name,
            pks=record_ids,
            fields_str='creator, content, state, substate, type, tags, priority, linked_ticket',
        )
        content_by_issue_id = self.seadb_service.get_thread_content(
            self.project_uuid,
            record_ids,
            query=portal_issue_comments_query(
                comments_table_name, SEARCH_TOOL_ISSUES_MAX_COMMENTS,
            ),
            bodies_by_thread_id={
                record_id: issue['content']
                for record_id, issue in issues_by_id.items()
            },
            budget=SEARCH_CONTENT_BUDGET,
        )

        related_usernames = []
        for record_id in record_id_record_map:
            issue = issues_by_id[record_id]
            issue_content = content_by_issue_id[record_id]
            related_usernames.append(issue['creator'])
            related_usernames.extend(
                message['creator']
                for message in chronological_messages(issue_content)
            )
        username_nickname_map = self.username_resolver.emails2nicknames(
            list(set(related_usernames)), return_map=True,
        )

        for record_id, record in record_id_record_map.items():
            issue = issues_by_id[record_id]
            issue_content = content_by_issue_id[record_id]
            image_source_contents = [issue['content'] or '']
            comments = [
                {
                    'creator': username_nickname_map.get(message['creator'], ''),
                    'via_agent': message.get('via_agent') is True,
                    'content': message['content'] or '',
                }
                for message in chronological_messages(issue_content)
            ]
            if issue_content.has_omitted_messages:
                comments.append(build_ticket_comment_notice(MORE_COMMENTS_OMITTED_NOTICE))
            image_source_contents.extend(comment['content'] for comment in comments)
            record['content'] = {
                'author': username_nickname_map.get(issue['creator'], ''),
                'content': issue_content.body,
                'comments': comments,
                'state': issue['state'],
                'substate': issue['substate'],
                'type': issue['type'],
                'tags': issue['tags'],
                'priority': issue['priority'],
                'linked_ticket': issue['linked_ticket'],
            }
            record['images'] = merge_image_urls(image_source_contents)
        return record_id_record_map
    
    ## github_issue
    def _get_github_issue_content(self, record_id_record_map, connection_id):
        topics_table_name = SchemaTables.GITHUB_ISSUES.table_name(connection_id)
        comments_table_name = SchemaTables.GITHUB_ISSUE_COMMENTS.table_name(connection_id)

        # query topics
        record_id_to_topics = self.seadb_service.get_records_by_pks(
            base_id=self.project_uuid,
            table_name=topics_table_name,
            pks=record_id_record_map.keys(),
            fields_str='issue_id, content, issue_number, state, state_reason, labels, issue_type, author, assignees, url, linked_ticket'
        )

        bodies_by_issue_id = {
            record['issue_id']: record.get('content')
            for record in record_id_to_topics.values()
        }
        content_by_issue_id = self.seadb_service.get_thread_content(
            self.project_uuid,
            bodies_by_issue_id.keys(),
            query=github_issue_comments_query(comments_table_name, SEARCH_TOOL_ISSUES_MAX_COMMENTS),
            bodies_by_thread_id=bodies_by_issue_id,
            budget=SEARCH_CONTENT_BUDGET,
        )

        # set content from topic and comments
        for record_id, record in record_id_record_map.items():
            record_from_seadb = record_id_to_topics[record_id]
            issue_content = content_by_issue_id[record_from_seadb['issue_id']]

            comments = [
                {'author': message['author'], 'content': message['content'] or ''}
                for message in chronological_messages(issue_content)
            ]
            if issue_content.has_omitted_messages:
                comments.append(build_author_comment_notice(MORE_COMMENTS_OMITTED_NOTICE))

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
                'content': issue_content.body,
                'comments': comments
            }

            record['content'] = content
        return record_id_record_map

    ## discourse_forum
    def _get_discourse_forum_content(self, record_id_record_map, connection_id):
        topics_table_name = SchemaTables.DISCOURSE_TOPICS.table_name(connection_id)
        comments_table_name = SchemaTables.DISCOURSE_REPLIES.table_name(connection_id)

        # query topics
        record_id_to_topics = self.seadb_service.get_records_by_pks(
            base_id=self.project_uuid,
            table_name=topics_table_name,
            pks=record_id_record_map.keys(),
            fields_str='topic_id, slug, resolved'
        )

        topic_ids = [record['topic_id'] for record in record_id_to_topics.values()]
        content_by_topic_id = self.seadb_service.get_thread_content(
            self.project_uuid,
            topic_ids,
            query=discourse_replies_query(comments_table_name, SEARCH_TOOL_ISSUES_MAX_COMMENTS),
            budget=SEARCH_CONTENT_BUDGET,
        )

        # set content from topic and comments
        for record_id, record in record_id_record_map.items():
            record_from_seadb = record_id_to_topics[record_id]
            topic_content = content_by_topic_id[record_from_seadb['topic_id']]

            comments = [
                {
                    'author': message['author'],
                    'post_number': message['post_number'],
                    'content': message['content'] or '',
                }
                for message in chronological_messages(topic_content)
            ]
            if topic_content.has_omitted_messages:
                comments.append(build_discourse_reply_notice(MORE_REPLIES_OMITTED_NOTICE))

            content = {
                'topic_id': record_from_seadb['topic_id'],
                'slug': record_from_seadb['slug'],
                'resolved': record_from_seadb['resolved'],
                'comments': comments
            }

            record['content'] = content
        return record_id_record_map

    ## email
    def _get_email_content(self, record_id_record_map, connection_id):
        threads_table_name = SchemaTables.THREAD.table_name(connection_id)
        replies_table_name = SchemaTables.EMAIL.table_name(connection_id)

        # query threads
        record_ids = record_id_record_map.keys()
        record_id_to_threads = self.seadb_service.get_records_by_pks(
            base_id=self.project_uuid,
            table_name=threads_table_name,
            pks=record_ids,
            fields_str='tags'
        )

        content_by_thread_id = self.seadb_service.get_thread_content(
            self.project_uuid,
            record_ids,
            query=email_messages_query(replies_table_name, SEARCH_TOOL_ISSUES_MAX_COMMENTS),
            budget=SEARCH_CONTENT_BUDGET,
        )

        # set content from topic and comments
        for record_id, record in record_id_record_map.items():
            record_from_seadb = record_id_to_threads[record_id]
            thread_content = content_by_thread_id[record_id]

            emails = [
                {
                    'title': message['title'],
                    'email_from': message['email_from'],
                    'email_to': message['email_to'],
                    'cc': message['cc'],
                    'content': message['content'] or '',
                }
                for message in chronological_messages(thread_content)
            ]
            if thread_content.has_omitted_messages:
                emails.append({
                    'title': None, 'email_from': None, 'email_to': None, 'cc': None,
                    'content': MORE_EMAILS_OMITTED_NOTICE,
                })

            content = {
                'topic_tags': record_from_seadb['tags'],
                'emails': emails
            }

            record['content'] = content
        return record_id_record_map


    # tasks
    ## general task
    def _get_general_task_content(self, record_id_record_map, connection_id):
        results_from_seadb = self.seadb_service.get_records_by_pks(
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

        record_id_to_issues = self.seadb_service.get_records_by_pks(
            base_id=self.project_uuid,
            table_name=issues_table_name,
            pks=record_id_record_map.keys(),
            fields_str='issue_id, identifier, content, state, labels, author, assignees, priority, due_date, linked_ticket'
        )

        bodies_by_issue_id = {
            record['issue_id']: record.get('content')
            for record in record_id_to_issues.values()
        }
        content_by_issue_id = self.seadb_service.get_thread_content(
            self.project_uuid,
            bodies_by_issue_id.keys(),
            query=linear_issue_comments_query(comments_table_name, SEARCH_TOOL_ISSUES_MAX_COMMENTS),
            bodies_by_thread_id=bodies_by_issue_id,
            budget=SEARCH_CONTENT_BUDGET,
        )

        for record_id, record in record_id_record_map.items():
            record_from_seadb = record_id_to_issues[record_id]
            issue_content = content_by_issue_id[record_from_seadb['issue_id']]

            comments = [
                {
                    'author': message['author'],
                    'created_time': message.get('created_time'),
                    'content': message['content'] or '',
                }
                for message in chronological_messages(issue_content)
            ]
            if issue_content.has_omitted_messages:
                comments.append(build_author_comment_notice(MORE_COMMENTS_OMITTED_NOTICE))

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
                'content': issue_content.body,
                'comments': comments
            }

            record['content'] = content
        return record_id_record_map
