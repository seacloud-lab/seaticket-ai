"""
DiscourseAgent: processes a single Discourse topic for one connection.

1. Build full context: topic body + replies.
2. Run the AI loop.
3. Store tool outcomes as actions in SeaDB.
"""
import json
import logging
from seaqa_ai.db.models import ProjectConnection
from seaqa_ai.agent.agents.connection_agent import ConnectionAgent
from seaqa_ai.agent.tools.discourse_tools import DISCOURSE_TOOL_CLASSES
from seaqa_ai.utils import uuid_str_to_32_chars
from seaqa_ai.seadb_models import SchemaTables
from seaqa_ai.agent.utils.constants import (
    MAX_CONTENT_CHARS,
    MAX_COMMENT_CHARS,
    MAX_COMMENTS,
    AgentSourceType,
)
import seaqa_ai.agent.prompts.discourse as discourse_prompts

logger = logging.getLogger(__name__)


class DiscourseAgent(ConnectionAgent):
    """Processes a Discourse topic for a single Discourse connection within a project."""

    SOURCE_TYPE = AgentSourceType.DISCOURSE_FORUM.value

    EVENT_TYPE_PROMPTS = discourse_prompts.PROMPTS

    SOURCE_TOOL_CLASSES = DISCOURSE_TOOL_CLASSES

    def __init__(self, app, project, connection_id: int, record_id=None, event=None):
        super().__init__(app, project, connection_id, record_id, event)
        self._topics_table = SchemaTables.DISCOURSE_TOPICS.table_name(self.connection_id)
        self._replies_table = SchemaTables.DISCOURSE_REPLIES.table_name(self.connection_id)

    # ── Abstract method implementations ────────────────────────────

    def get_item(self) -> dict | None:
        if self.record_id is None:
            return None
        conditions = [
            f"_pk = {self.record_id}",
            "(`deleted` = False OR `deleted` IS NULL)",
            "(`resolved` = False OR `resolved` IS NULL)",
            "(`linked_ticket` = 0 OR `linked_ticket` IS NULL)",
        ]
        where = ' AND '.join(conditions)
        sql = (
            f"SELECT `_pk`, `title`, `topic_id`, `resolved`, "
            f"`created_time`, `modified_time`, `linked_ticket`, `slug` "
            f"FROM `{self._topics_table}` WHERE {where} LIMIT 1"
        )
        results = self.seadb_api.query_rows(self.project_uuid, sql).get('results', [])
        return results[0] if results else None

    def get_item_context(self, item: dict) -> dict:
        comments = self._get_topic_comments(item)
        return {'comments': comments}

    def build_record_payload(self, item: dict, item_context: dict) -> dict:
        comments = item_context.get('comments', [])
        topic_content = ''
        topic_author = 'N/A'
        recent_comments: list[dict] = []

        if comments:
            first_post = comments[0]
            topic_author = first_post.get('author', 'N/A')
            topic_content = (first_post.get('content') or '').strip()[:MAX_CONTENT_CHARS]
            subsequent_comments = comments[1:]
            recent_comments = [self._format_comment_entry(r) for r in subsequent_comments[-MAX_COMMENTS:]]

        return {
            'title': item.get('title', 'Untitled'),
            'topic_id': item.get('topic_id', 'N/A'),
            'resolved': item.get('resolved', False),
            'author': topic_author,
            'created_time': item.get('created_time', 'N/A'),
            'modified_time': item.get('modified_time', 'N/A'),
            'url': self._build_topic_url(item),
            'linked_ticket': item.get('linked_ticket', 'N/A'),
            'content': topic_content[:MAX_CONTENT_CHARS] if topic_content else '',
            'recent_comments': recent_comments,
        }

    # ── Discourse-specific helpers ──────────────────────────────────

    @staticmethod
    def _format_comment_entry(r: dict) -> dict:
        return {
            'author': r.get('author', '?'),
            'modified_time': r.get('modified_time', '?'),
            'content': (r.get('content') or '').strip()[:MAX_COMMENT_CHARS],
            'post_number': r.get('post_number'),
        }

    def _build_topic_url(self, item: dict) -> str:
        slug = item.get('slug')
        topic_id = item.get('topic_id')
        if not slug or topic_id is None:
            return 'N/A'

        forum_url = self._get_forum_url()
        if forum_url:
            return forum_url.rstrip('/') + f'/t/{slug}/{topic_id}'
        return f'/t/{slug}/{topic_id}'

    def _get_forum_url(self) -> str:
        forum_url = ''
        with self.db_session_class() as db_session:
            try:
                project_uuid_32 = uuid_str_to_32_chars(self.project_uuid)
                row = db_session.query(ProjectConnection.config).filter(
                    ProjectConnection.id == self.connection_id,
                    ProjectConnection.project_uuid == project_uuid_32,
                    ProjectConnection.deleted == 0,
                ).first()
                if row and row[0]:
                    config_dict = json.loads(row[0])
                    if isinstance(config_dict, dict):
                        forum_url = str(config_dict.get('url') or '').strip()
            except Exception as e:
                logger.warning('Failed to resolve forum url for connection %s: %s', self.connection_id, e)

        return forum_url

    def _get_topic_comments(self, topic: dict) -> list:
        topic_id = topic.get('topic_id')
        if not topic_id:
            return []

        replies_map = self.seadb_api.get_first_and_latest_topic_entries(
            base_id=self.project_uuid,
            table_name=self._replies_table,
            topic_ids=[topic_id],
            topic_id_field_name='topic_id',
            fields_str='post_number, author, modified_time, content',
            has_deleted_field=False,
            first_order_by_field='post_number',
            latest_order_by_field='post_number',
            limit_for_each_topic=MAX_COMMENTS,
            dedupe_key_fields=['post_number'],
            sort_key_fields=['post_number'],
        )
        return replies_map.get(topic_id, [])
