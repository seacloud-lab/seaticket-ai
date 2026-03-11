import json
import logging

from seaqa_ai.db.models import ProfileProfile
from seaqa_ai.utils import normalize_usernames

logger = logging.getLogger(__name__)


class UsernameResolver:
    """Resolve SeaQA assignee/user IDs into readable display names."""

    def __init__(self, db_session_class):
        self.db_session_class = db_session_class
        self.cache = {}

    def email2nickname(self, email):
        """Resolve one email/user string to readable nickname."""
        if not email:
            return ''
        user = str(email).strip()
        if not user:
            return ''
        self._warmup_nickname_cache([user])
        return self.cache.get(user) or self._format_display_name(user, None, None)

    def emails2nicknames(self, emails, return_map=False):
        """Resolve assignees and keep original ordering of values."""
        users = normalize_usernames(emails)
        if not users:
            return 'N/A'

        self._warmup_nickname_cache(list(set(users)))

        resolved_names = {
            user: self.cache.get(user) or self._format_display_name(user, None, None)
            for user in users
        }

        if return_map:
            return resolved_names

        return ', '.join(list(resolved_names.values())) if resolved_names else 'N/A'

    def _format_display_name(self, user, nickname, contact_email):
        if nickname:
            nickname_str = str(nickname).strip()
            if nickname_str:
                return nickname_str

        email_source = contact_email or user
        if not email_source:
            return ''
        email_source = str(email_source).strip()
        if not email_source:
            return ''
        return email_source.split('@', 1)[0]

    def _warmup_nickname_cache(self, users):
        """Bulk query profile_profile and populate assignee display cache."""
        users_to_query = [u for u in users if u and u not in self.cache]
        if not users_to_query:
            return

        with self.db_session_class() as db_session:
            try:
                profiles = ProfileProfile.get_profiles_by_users(db_session, users_to_query)
                for profile in profiles:
                    user = str(profile['user'])
                    self.cache[user] = self._format_display_name(
                        user=user,
                        nickname=profile['nickname'],
                        contact_email=profile['contact_email']
                    )
            except Exception as e:
                logger.exception(f'Failed to resolve assignees from MySQL: {e}')

    def _convert_username_like_value(self, value):
        """Convert value that may contain user IDs (string/list) to readable form."""
        if isinstance(value, list):
            query_list = [v for v in value if isinstance(v, str)]
            username_nickname_map = self.emails2nicknames(query_list, True)
            results = []
            for v in value:
                if isinstance(v, str) and v.strip() in username_nickname_map:
                    results.append(username_nickname_map[v.strip()])
                else:
                    results.append(v)
            return results
        if isinstance(value, str):
            return self.email2nickname(value)
        return value
    
    def convert_usernames_fields_to_nicknames(self, detail):
        """Convert user IDs in activity detail JSON string/object to readable names."""
        if not detail:
            return detail

        parsed = detail
        parsed_from_str = False
        if isinstance(detail, str):
            try:
                parsed = json.loads(detail)
                parsed_from_str = True
            except Exception:
                return detail

        def _walk(node):
            if isinstance(node, list):
                return [_walk(item) for item in node]
            if isinstance(node, dict):
                field_name = node.get('field_name')
                converted = {}
                for key, value in node.items():
                    if key in ('creator', 'assignee', 'assignees', 'user', 'author'):
                        converted[key] = self._convert_username_like_value(value)
                    elif key in ('old_value', 'new_value') and field_name == 'assignees':
                        converted[key] = self._convert_username_like_value(value)
                    else:
                        converted[key] = _walk(value)
                return converted
            return node

        converted = _walk(parsed)
        if parsed_from_str:
            return json.dumps(converted, ensure_ascii=False)
        return converted
