"""In-memory suggestion state edited during a single regeneration turn.

Suggestions use two stable identity prefixes across turns:
- ``action:<pk>``: an existing ``agent_actions`` row in SeaDB (status ``pending`` or ``failed``).
- ``draft:<uuid32>``: an unpersisted suggestion added during the regeneration conversation.
"""

import json
import re
from copy import deepcopy
from uuid import uuid4

from seaqa_ai.agent.utils.suggestion_validation import (
    apply_suggestion_changes,
    persisted_tool_names,
    validate_client_draft,
)


SUGGESTION_FIELDS = (
    'tool_name', 'suggestion_content', 'suggestion_reason', 'suggestion_payload',
    'target_item_type', 'target_item_id', 'target_item_title',
)
SUGGESTION_ID_RE = re.compile(r'(action:[1-9][0-9]*|draft:[0-9a-f]{32})')
ACTION_ID_RE = re.compile(r'action:[1-9][0-9]*')


def suggestion_key(draft):
    """Uniqueness key preventing two active suggestions of the same action on one target."""
    return tuple(str(draft.get(field) or '') for field in ('tool_name', 'target_item_type', 'target_item_id'))


def normalize_draft(row):
    """Coerce a raw DB row or client draft dict into a canonical editable suggestion dict."""
    if not isinstance(row, dict):
        raise ValueError('Each suggestion must be an object')
    draft = {
        field: deepcopy(row[field]) if row.get(field) is not None else ({} if field == 'suggestion_payload' else '')
        for field in SUGGESTION_FIELDS
    }
    # DB rows store suggestion_payload as a JSON string; client drafts send a dict.
    payload = draft['suggestion_payload']
    if isinstance(payload, str):
        try:
            payload = json.loads(payload or '{}')
        except ValueError as error:
            raise ValueError('suggestion_payload must be a JSON object') from error
    if not isinstance(payload, dict):
        raise ValueError('suggestion_payload must be an object')
    draft['suggestion_payload'] = payload
    for field in SUGGESTION_FIELDS:
        if field == 'suggestion_payload':
            continue
        if field == 'target_item_id':
            draft[field] = str(draft[field])
        if not isinstance(draft[field], str):
            raise ValueError(f'{field} must be a string')
    if not all(draft[field] for field in ('tool_name', 'target_item_type', 'target_item_id')):
        raise ValueError('Suggestions require a tool and a target')
    draft.update({
        'status': row.get('status') or 'pending',
        'action_type': 'suggestion',
        'phase': row.get('phase') or 'regeneration',
    })
    # Executed or cancelled suggestions are immutable history and must never enter the store.
    if draft['status'] not in ('pending', 'failed'):
        raise ValueError('Only pending and failed suggestions may be edited')
    return draft

TICKET_EXCLUSIVE_TOOLS = {'suggest_create_ticket', 'suggest_link_existing_ticket'}

def _is_mutually_exclusive(tool_a, tool_b):
    if 'suggest_move_to_spam' in (tool_a, tool_b):
        return True
    return tool_a in TICKET_EXCLUSIVE_TOOLS and tool_b in TICKET_EXCLUSIVE_TOOLS

class SuggestionDraftStore:
    """Track preserved, modified, added, and removed suggestions across regeneration turns.

    By holding the full collection in memory and returning ``snapshot()`` at the end
    of the turn, suggestions that the model leaves untouched are preserved verbatim
    without requiring the model to recreate them.
    """

    def __init__(
        self,
        original_suggestions=None,
        previous_drafts=None,
        removed_suggestion_ids=None,
        *,
        allowed_tools=None,
    ):
        originals = original_suggestions if original_suggestions is not None else []
        previous = previous_drafts if previous_drafts is not None else []
        removed = removed_suggestion_ids if removed_suggestion_ids is not None else []
        if not isinstance(originals, list) or not isinstance(previous, list) or not isinstance(removed, list):
            raise ValueError('Suggestion snapshots must be arrays')

        # Map event creation tools (e.g. suggest_reply_external_targets) to persisted tool names (suggest_reply).
        self._allowed_tools = persisted_tool_names(allowed_tools)

        # 1. Index authoritative editable rows from SeaDB as action:<pk>.
        self._originals = {}
        for row in originals:
            draft = normalize_draft(row)
            pk = row.get('_pk', row.get('id'))
            if isinstance(pk, bool) or not isinstance(pk, int) or pk <= 0:
                raise ValueError('Original suggestions require a valid action ID')
            draft['suggestion_id'] = f'action:{pk}'
            if draft['suggestion_id'] in self._originals:
                raise ValueError('Duplicate original suggestion ID')
            self._originals[draft['suggestion_id']] = draft

        # 2. Record persisted suggestions already removed in earlier turns of this chat.
        # Unpersisted draft:<uuid> items vanish immediately on removal and are not tracked here.
        self._removed_ids = []
        seen_removed = set()
        for suggestion_id in removed:
            if not isinstance(suggestion_id, str) or not ACTION_ID_RE.fullmatch(suggestion_id):
                raise ValueError('removed_suggestion_ids must contain persisted suggestion IDs')
            if suggestion_id in seen_removed:
                raise ValueError('Duplicate removed suggestion_id')
            if suggestion_id not in self._originals:
                raise ValueError('Suggestion source is unavailable or no longer editable; refresh the run')
            seen_removed.add(suggestion_id)
            self._removed_ids.append(suggestion_id)

        # 3. Seed working drafts from the client's previous turn (if any), or from DB originals
        # on the very first regeneration turn.
        self._drafts = {}
        for row in previous or [
            original for suggestion_id, original in self._originals.items()
            if suggestion_id not in seen_removed
        ]:
            if not isinstance(row, dict):
                raise ValueError('Each suggestion must be an object')
            suggestion_id = row.get('suggestion_id')
            if suggestion_id in seen_removed:
                continue
            draft = normalize_draft(row)
            if suggestion_id is None:
                # Backward compatibility for legacy client payloads without suggestion_id:
                # match against an unremoved original by (tool, target_type, target_id), or treat as new.
                matches = [
                    key for key, original in self._originals.items()
                    if key not in seen_removed and suggestion_key(original) == suggestion_key(draft)
                ]
                if len(matches) > 1:
                    raise ValueError('Ambiguous suggestion source; refresh the run')
                suggestion_id = matches[0] if matches else f'draft:{uuid4().hex}'
            if suggestion_id in seen_removed:
                continue
            if not isinstance(suggestion_id, str) or not SUGGESTION_ID_RE.fullmatch(suggestion_id):
                raise ValueError('Invalid suggestion_id')
            if suggestion_id in self._drafts:
                raise ValueError('Duplicate suggestion_id')
            original = self._originals.get(suggestion_id)
            if suggestion_id.startswith('action:'):
                if original is None:
                    raise ValueError('Suggestion source is unavailable or no longer editable; refresh the run')
                if suggestion_key(draft) != suggestion_key(original):
                    raise ValueError('Suggestion type and target cannot be changed')
                # Never trust client-supplied status or target title for persisted rows.
                draft['status'] = original['status']
                draft['target_item_title'] = original['target_item_title']
            draft['suggestion_id'] = suggestion_id
            self._drafts[suggestion_id] = draft

        # 4. Backfill any unremoved DB originals missing from previous_drafts
        # (for example, if a new pending action appeared or a client omitted an item).
        # Omission is never treated as deletion; only explicit removed_suggestion_ids stay out.
        for suggestion_id, original in self._originals.items():
            if suggestion_id in seen_removed:
                continue
            self._drafts.setdefault(suggestion_id, deepcopy(original))

        # 5. Ensure unpersisted client drafts do not collide with preserved originals or each other.
        keys = {
            suggestion_key(draft)
            for suggestion_id, draft in self._originals.items()
            if suggestion_id not in seen_removed
        }
        for suggestion_id, draft in self._drafts.items():
            if suggestion_id in self._originals:
                continue
            if suggestion_key(draft) in keys:
                raise ValueError('Duplicate suggestion action and target')
            keys.add(suggestion_key(draft))

    def validate_previous_drafts(self, context):
        """Validate client-supplied drafts and removals from earlier turns once records are loaded.

        Called by ``AgentReACT`` after record observations are injected into ``context``,
        because checking ``related_ticket`` or ``suggested_labels`` requires loaded records.
        """
        for suggestion_id in self._removed_ids:
            self._ensure_allowed(self._originals[suggestion_id])
        for suggestion_id, draft in self._drafts.items():
            original = self._originals.get(suggestion_id)
            if original is not None and draft == original:
                # Untouched DB suggestions are preserved verbatim without revalidation.
                continue
            self._ensure_allowed(draft)
            validate_client_draft(draft, context, original=original)

    def _ensure_allowed(self, draft):
        if draft['tool_name'] not in self._allowed_tools:
            raise ValueError('This event does not permit editing that suggestion type')

    def snapshot(self):
        """Return a deep copy of all currently active drafts."""
        return deepcopy(list(self._drafts.values()))

    def removed_ids(self):
        """Return persisted ``action:<pk>`` IDs marked for cancellation when applied."""
        return list(self._removed_ids)

    def add_result(self, tool_name, result, targets):
        """Append new ``draft:<uuid>`` entries produced by a ``suggest_*`` creation tool.

        All targets from a multi-target tool call (such as ``suggest_reply_external_targets``)
        are validated together before mutating ``self._drafts`` so a failed check leaves state clean.
        """
        drafts = []
        keys = {suggestion_key(draft) for draft in self._drafts.values()}
        for target in targets:
            draft = normalize_draft({
                'tool_name': result.get('trace_tool_name') or tool_name,
                'suggestion_content': result.get('suggestion_content') or '',
                'suggestion_reason': result.get('suggestion_reason') or '',
                'suggestion_payload': result.get('suggestion_payload') or {},
                'target_item_type': target['source_type'],
                'target_item_id': target['source_id'],
                'target_item_title': target['source_title'],
            })
            self._ensure_allowed(draft)
            key = suggestion_key(draft)
            if key in keys:
                raise ValueError('A suggestion already exists for this action and target; use modify_suggestion with its ID')
            if any(
                (other['target_item_type'], other['target_item_id']) == (draft['target_item_type'], draft['target_item_id'])
                and _is_mutually_exclusive(draft['tool_name'], other['tool_name'])
                for other in list(self._drafts.values()) + drafts
            ):
                raise ValueError('A mutually exclusive suggestion already exists for this target')
            keys.add(key)
            draft['suggestion_id'] = f'draft:{uuid4().hex}'
            drafts.append(draft)
        for draft in drafts:
            self._drafts[draft['suggestion_id']] = draft
        return deepcopy(drafts)

    def modify(self, suggestion_id, changes, context):
        """Apply a partial field patch to one active draft via ``modify_suggestion``."""
        draft = self._drafts.get(suggestion_id)
        if draft is None:
            raise ValueError('Unknown suggestion_id; use an ID from the current drafts')
        self._ensure_allowed(draft)
        candidate = apply_suggestion_changes(draft, changes, context)
        changed = candidate != draft
        self._drafts[suggestion_id] = candidate
        return {'status': 'completed', 'changed': changed, 'suggestion': deepcopy(candidate)}

    def remove(self, suggestion_id, reason):
        """Remove an active draft via ``remove_suggestion`` and free its target slot.

        Unpersisted ``draft:<uuid>`` items are simply discarded; persisted ``action:<pk>``
        items are also recorded in ``_removed_ids`` so the web backend cancels them on Apply.
        """
        if not isinstance(suggestion_id, str) or not SUGGESTION_ID_RE.fullmatch(suggestion_id):
            raise ValueError('Invalid suggestion_id')
        if not isinstance(reason, str) or not reason.strip():
            raise ValueError('reason must not be empty')
        if suggestion_id in self._removed_ids and suggestion_id not in self._drafts:
            return {'status': 'completed', 'changed': False}
        draft = self._drafts.get(suggestion_id)
        if draft is None:
            raise ValueError('Unknown suggestion_id; use an ID from the current drafts')
        self._ensure_allowed(draft)
        self._drafts.pop(suggestion_id)
        if suggestion_id.startswith('action:') and suggestion_id not in self._removed_ids:
            self._removed_ids.append(suggestion_id)
        return {'status': 'completed', 'changed': True}
