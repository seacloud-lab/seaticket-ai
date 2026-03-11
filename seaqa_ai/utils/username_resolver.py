import ast
import json
import logging

from seaqa_ai.db.models import ProfileProfile

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
        self._warmup_assignee_display_cache([user])
        return self.cache.get(user) or self._format_display_name(user, None, None)

    def emails2nicknames(self, emails):
        """Resolve assignees and keep original ordering of values."""
        users = self._extract_assignee_users(emails)
        if not users:
            return 'N/A'

        # Keep first occurrence order while deduplicating.
        seen = set()
        unique_users = []
        for user in users:
            if user not in seen:
                seen.add(user)
                unique_users.append(user)

        self._warmup_assignee_display_cache(unique_users)

        resolved_names = []
        for user in users:
            cached_name = self.cache.get(user)
            if cached_name:
                resolved_names.append(cached_name)
            else:
                resolved_names.append(self._format_display_name(user, None, None))

        return ', '.join(resolved_names) if resolved_names else 'N/A'

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
                        converted[key] = self._convert_user_like_value(value)
                    elif key in ('old_value', 'new_value') and field_name == 'assignees':
                        converted[key] = self._convert_user_like_value(value)
                    else:
                        converted[key] = _walk(value)
                return converted
            return node

        converted = _walk(parsed)
        if parsed_from_str:
            return json.dumps(converted, ensure_ascii=False)
        return converted

    def _extract_assignee_users(self, assignees_raw):
        """Normalize ticket assignees to a list of user IDs/emails."""
        if not assignees_raw:
            return []
        if isinstance(assignees_raw, list):
            return [str(v).strip() for v in assignees_raw if str(v).strip()]
        if not isinstance(assignees_raw, str):
            value = str(assignees_raw).strip()
            return [value] if value else []

        value = assignees_raw.strip()
        if not value:
            return []

        # Keep raw string on parse failure; parsed can be list/dict/etc on success.
        parsed = value
        if value.startswith(('[', '{')):
            try:
                parsed = json.loads(value)
            except Exception:
                try:
                    parsed = ast.literal_eval(value)
                except Exception:
                    parsed = value

        if isinstance(parsed, list):
            return [str(v).strip() for v in parsed if str(v).strip()]

        # Fallback for comma/semicolon separated legacy formats.
        split_values = []
        for sep in (',', ';'):
            if sep in value:
                split_values = [v.strip() for v in value.split(sep) if v.strip()]
                break
        if split_values:
            return split_values
        return [value]

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

    def _warmup_assignee_display_cache(self, users):
        """Bulk query profile_profile and populate assignee display cache."""
        users_to_query = [u for u in users if u and u not in self.cache]
        if not users_to_query:
            return

        session = self.db_session_class()
        try:
            profiles = ProfileProfile.get_profiles_by_users(session, users_to_query)
            for profile in profiles:
                user = str(profile['user'])
                self.cache[user] = self._format_display_name(
                    user=user,
                    nickname=profile['nickname'],
                    contact_email=profile['contact_email']
                )
        except Exception as e:
            logger.exception(f'Failed to resolve assignees from MySQL: {e}')
        finally:
            session.close()

    def _convert_user_like_value(self, value):
        """Convert value that may contain user IDs (string/list) to readable form."""
        if isinstance(value, list):
            return [self.email2nickname(v) if isinstance(v, str) else v for v in value]
        if isinstance(value, str):
            return self.email2nickname(value)
        return value
