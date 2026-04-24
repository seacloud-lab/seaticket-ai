"""
DiscourseAgent: processes a single Discourse topic for one connection.

1. Build full context: topic body + replies.
2. Run the AI loop.
3. Store tool outcomes as actions in SeaDB.
"""
import json
import logging
from seaqa_ai.db.models import ProjectConnection
from seaqa_ai.agent.connection_agent import ConnectionAgent
from seaqa_ai.agent.tools.discourse_tools import DISCOURSE_TOOL_CLASSES
from seaqa_ai.utils import uuid_str_to_32_chars
from seaqa_ai.utils.constants import ConnectionType
from seaqa_ai.utils.seadb_api import gen_seadb_table_name
from seaqa_ai.agent.utils.constants import (
    MAX_CONTENT_CHARS,
    MAX_COMMENT_CHARS,
    SourceType,
    DataEventType,
)
from seaqa_ai.agent.system_prompt import (
    DISCOURSE_ANALYSIS_SYSTEM_PROMPT,
    DISCOURSE_HANDLING_SYSTEM_PROMPT,
)

logger = logging.getLogger(__name__)


class DiscourseAgent(ConnectionAgent):
    """Processes a Discourse topic for a single Discourse connection within a project."""

    SOURCE_TYPE = SourceType.DISCOURSE_FORUM.value

    def __init__(self, app, project, connection_id: int, record_id=None, events=None):
        super().__init__(app, project, connection_id, record_id, events)
        self._topics_table, self._replies_table = gen_seadb_table_name(ConnectionType.DISCOURSE_FORUM.value, self.connection_id)

    def get_analysis_system_prompt(self) -> str:
        return self._build_system_prompt(DISCOURSE_ANALYSIS_SYSTEM_PROMPT)

    def get_handling_system_prompt(self) -> str:
        return self._build_system_prompt(DISCOURSE_HANDLING_SYSTEM_PROMPT)

    def get_tool_classes(self) -> list:
        return DISCOURSE_TOOL_CLASSES + self.SEARCH_TOOL_CLASSES

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
        sql = f"SELECT * FROM `{self._topics_table}` WHERE {where} LIMIT 1"
        results = self.seadb_api.query_rows(self.project_uuid, sql).get('results', [])
        return results[0] if results else None

    def get_item_context(self, item: dict) -> dict:
        replies = self._get_topic_replies(item)
        return {'replies': replies}

    def build_item_message(self, item: dict, item_context: dict) -> str:
        source_id = f'{self.connection_id}_{item["_pk"]}'

        replies = item_context.get('replies', [])
        topic_content = ''
        topic_author = 'N/A'
        formatted_replies = []
        new_reply_post_numbers = self._collect_new_reply_post_numbers()
        if replies:
            first_post = replies[0]
            topic_author = first_post.get('author', 'N/A')
            topic_content = (first_post.get('content') or '').strip()[:MAX_CONTENT_CHARS]

            for r in replies[1:][-10:]:
                entry = {
                    'author': r.get('author', '?'),
                    'modified_time': r.get('modified_time', '?'),
                    'content': (r.get('content') or '').strip()[:MAX_COMMENT_CHARS],
                }
                if new_reply_post_numbers and r.get('post_number') in new_reply_post_numbers:
                    entry['is_new_reply'] = True
                formatted_replies.append(entry)

        message_dict = {
            'source_id': source_id,
            'events': self.events,
            'title': item.get('title', 'Untitled'),
            'topic_id': item.get('topic_id', 'N/A'),
            'resolved': item.get('resolved', False),
            'author': topic_author,
            'views': item.get('views', 0),
            'created_time': item.get('created_time', 'N/A'),
            'modified_time': item.get('modified_time', 'N/A'),
            'url': self._build_topic_url(item),
            'linked_ticket': item.get('linked_ticket', 'N/A'),
            'content': topic_content[:MAX_CONTENT_CHARS] if topic_content else '',
            'recent_replies': formatted_replies,
        }

        return json.dumps(message_dict, ensure_ascii=False, indent=2)

    # ── Discourse-specific helpers ──────────────────────────────────

    def _is_reply_only_trigger(self) -> bool:
        event_types = {e.get('type') for e in (self.events or []) if isinstance(e, dict)}
        return (
            DataEventType.DISCOURSE_TOPIC_REPLY_ADDED.value in event_types
            and DataEventType.DISCOURSE_TOPIC_ADDED.value not in event_types
        )

    def _collect_new_reply_post_numbers(self) -> set:
        if not self._is_reply_only_trigger():
            return set()

        new_reply_post_numbers = set()
        for e in (self.events or []):
            if not isinstance(e, dict):
                continue
            if e.get('type') != DataEventType.DISCOURSE_TOPIC_REPLY_ADDED.value:
                continue
            replies = ((e.get('new_value') or {}).get('replies') or [])
            for r in replies:
                if not isinstance(r, dict):
                    continue
                post_number = r.get('post_number')
                if post_number is None:
                    continue
                new_reply_post_numbers.add(post_number)
        return new_reply_post_numbers

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

    def _get_topic_replies(self, topic: dict) -> list:
        topic_id = topic.get('topic_id')
        if not topic_id:
            return []

        sql = (
            f"SELECT * FROM `{self._replies_table}` "
            f"WHERE `topic_id` = {topic_id} "
            f"ORDER BY `post_number` ASC"
        )
        return self.seadb_api.query_rows(self.project_uuid, sql).get('results', [])
