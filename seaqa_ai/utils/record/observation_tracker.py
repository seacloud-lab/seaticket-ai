"""Per-request observation tracking, scope deduplication, and reporting budget."""

from seaqa_ai.utils.record.identity import record_key


class RecordObservationTracker:
    """Tracks which records have been shown to an LLM, in which scopes, and with what citations.

    One tracker instance spans an entire Chat turn or multi-phase Agent run:
    - Chat uses a single scope (``'chat'``), so reading the same record twice in one
      turn hits ``in_scope(...) == True`` and returns a duplicate notice.
    - Agent assigns a fresh UUID ``scope`` per phase (Prelude, Analysis, Handling),
      because each phase starts with an empty LLM context. A record read in Prelude
      has ``in_scope(..., analysis_scope) == False`` (so Analysis receives full content
      again), while ``was_reported(...) == True`` (so it does NOT consume a second slot
      from ``GET_RECORDS_MAX_RECORDS``).

    Example:
        >>> tracker = RecordObservationTracker()
        >>> ticket = {'source_type': 'ticket', 'connection_id': None, 'record_id': 100}
        >>>
        >>> # 1. Prelude pre-injects Ticket #100 without registering a citation (reference_id=None).
        >>> tracker.mark_reported(ticket, scope='scope_prelude', reference_id=None)
        >>> tracker.reported_count
        1
        >>> tracker.in_scope(ticket, 'scope_prelude')
        True
        >>>
        >>> # 2. Analysis starts with a new LLM memory ('scope_analysis'):
        >>> #    - Not in current scope yet -> provide full record content to this phase.
        >>> #    - Already reported in this run -> free re-read, does not deduct another slot.
        >>> tracker.in_scope(ticket, 'scope_analysis')
        False
        >>> tracker.was_reported(ticket)
        True
        >>>
        >>> # 3. Model actively reads Ticket #100 in Analysis with citation '<reference_2>'.
        >>> tracker.mark_reported(ticket, scope='scope_analysis', reference_id='<reference_2>')
        '<reference_2>'
        >>> tracker.reported_count  # Still 1 distinct record across the whole run!
        1
        >>> tracker.reference_id(ticket, 'scope_prelude')   # Prelude remains None
        >>> tracker.reference_id(ticket, 'scope_analysis')  # Analysis gets '<reference_2>'
        '<reference_2>'
    """

    def __init__(self):
        # Maps record_key -> {observation_scope: reference_id | None}
        # e.g. {'ticket-ticket-100': {'scope_prelude': None, 'scope_analysis': '<reference_2>'}}
        self._scopes_by_record: dict[str, dict[str, str | None]] = {}

    def in_scope(self, identity: dict, scope: str) -> bool:
        """Return True if this record's content was already provided in ``scope``."""
        scope_refs = self._scopes_by_record.get(record_key(**identity))
        return bool(scope_refs and scope in scope_refs)

    def reference_id(self, identity: dict, scope: str) -> str | None:
        """Return the citation label (e.g. ``'<reference_0>'``) bound in ``scope``, or None."""
        scope_refs = self._scopes_by_record.get(record_key(**identity))
        return scope_refs.get(scope) if scope_refs else None

    def was_reported(self, identity: dict) -> bool:
        """Return True if this record was already provided in ANY scope of the current run."""
        return record_key(**identity) in self._scopes_by_record

    @property
    def reported_count(self) -> int:
        """Total number of distinct records reported across all scopes (used for slot budget)."""
        return len(self._scopes_by_record)

    def mark_reported(self, identity: dict, scope: str, reference_id: str | None = None) -> str | None:
        """Record that ``identity`` was provided in ``scope`` and commit its budget slot.

        If ``scope`` already holds a non-None citation label and ``reference_id`` is None,
        the existing citation label for that scope is preserved.
        """
        key = record_key(**identity)
        scope_refs = self._scopes_by_record.setdefault(key, {})
        if reference_id is not None or scope not in scope_refs:
            scope_refs[scope] = reference_id
        return scope_refs[scope]
