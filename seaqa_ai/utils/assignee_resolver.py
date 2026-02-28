import json
import logging

from sqlalchemy import bindparam, text  # pyright: ignore[reportMissingImports]

logger = logging.getLogger(__name__)


class AssigneeResolver:
    """Resolve SeaQA assignee/user IDs into readable display names."""

    def __init__(self, db_session_class, cache=None):
        self.db_session_class = db_session_class
        self.cache = cache if cache is not None else {}

    def extract_assignee_users(self, assignees_raw):
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

        parsed = None
        if value.startswith('['):
            try:
                parsed = json.loads(value)
            except Exception:
                parsed = None

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

    def build_assignee_display_name(self, user, nickname, contact_email):
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

    def warmup_assignee_display_cache(self, users):
        """Bulk query profile_profile and populate assignee display cache."""
        users_to_query = [u for u in users if u and u not in self.cache]
        if not users_to_query:
            return

        session = self.db_session_class()
        try:
            result = session.execute(
                text(
                    "SELECT `user`, `nickname`, `contact_email` "
                    "FROM `profile_profile` "
                    "WHERE `user` IN :users"
                ).bindparams(bindparam('users', expanding=True)),
                {'users': users_to_query}
            )
            rows = result.fetchall()
            for row in rows:
                user = str(row[0])
                self.cache[user] = self.build_assignee_display_name(
                    user=user,
                    nickname=row[1],
                    contact_email=row[2]
                )
        except Exception as e:
            logger.exception(f'Failed to resolve assignees from MySQL: {e}')
        finally:
            session.close()

    def resolve_user_for_display(self, user):
        """Resolve a single user to readable display name."""
        if not user:
            return ''
        user = str(user).strip()
        if not user:
            return ''
        self.warmup_assignee_display_cache([user])
        return self.cache.get(user) or self.build_assignee_display_name(user, None, None)

    def convert_user_like_value(self, value):
        """Convert value that may contain user IDs (string/list) to readable form."""
        if isinstance(value, list):
            return [self.resolve_user_for_display(v) if isinstance(v, str) else v for v in value]
        if isinstance(value, str):
            return self.resolve_user_for_display(value)
        return value

    def convert_activity_detail(self, detail):
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
                        converted[key] = self.convert_user_like_value(value)
                    elif key in ('old_value', 'new_value') and field_name == 'assignees':
                        converted[key] = self.convert_user_like_value(value)
                    else:
                        converted[key] = _walk(value)
                return converted
            return node

        converted = _walk(parsed)
        if parsed_from_str:
            return json.dumps(converted, ensure_ascii=False)
        return converted

    def resolve_assignees_for_display(self, assignees_raw):
        """Resolve assignees and keep original ordering of values."""
        users = self.extract_assignee_users(assignees_raw)
        if not users:
            return 'N/A'

        # Keep first occurrence order while deduplicating.
        seen = set()
        unique_users = []
        for user in users:
            if user not in seen:
                seen.add(user)
                unique_users.append(user)

        self.warmup_assignee_display_cache(unique_users)

        resolved_names = []
        for user in users:
            cached_name = self.cache.get(user)
            if cached_name:
                resolved_names.append(cached_name)
            else:
                resolved_names.append(self.build_assignee_display_name(user, None, None))

        return ', '.join(resolved_names) if resolved_names else 'N/A'
