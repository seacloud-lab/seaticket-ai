"""Indexed pool of citable record sources shared by Search and GetRecords."""

from seaqa_ai.utils.constants import ExtraSourceType
from seaqa_ai.utils.record.identity import record_key


_RECORD_SOURCE_METADATA_FIELDS = (
    'title', 'url', 'modified_time', 'state', 'resolved', 'linked_ticket',
)
_FEATURED_SOURCE_METADATA_FIELDS = ('title', 'ai_summary', 'tags', 'modified_time')


class ReferencePool:
    """Append-only indexed pool mapping distinct records to ``<reference_i>`` labels.

    One ``ReferencePool`` spans an entire Chat turn or multi-phase Agent run so
    search tools and ``get_records`` share stable citation labels:
    - Index ``i`` permanently maps to ``<reference_{i}>`` (items are never removed
      or reordered).
    - If a record was first discovered via search as ``<reference_0>`` and later
      read in full via ``get_records``, ``register_record`` reuses ``<reference_0>``
      and enriches the existing entry with full-content image URLs and fresh metadata.
    - At the end of a turn/phase, ``get_answer_and_sources`` resolves only the
      ``<reference_i>`` markers actually cited in the model's final text.

    Example:
        >>> pool = ReferencePool()
        >>> # 1. Search discovers GitHub Issue #50 -> assigned '<reference_0>'
        >>> pool.register_search_result({'type': 'github_issue', 'connection_id': 7, '_id': 50, 'title': 'Sync 403'})
        '<reference_0>'
        >>>
        >>> # 2. get_records reads GitHub Issue #50 in full -> reuses '<reference_0>' and attaches images
        >>> identity = {'source_type': 'github_issue', 'connection_id': 7, 'record_id': 50}
        >>> pool.register_record(identity, {'title': 'Sync 403', 'url': 'https://...'}, image_urls=['https://.../err.png'])
        '<reference_0>'
        >>> len(pool)
        1
        >>> pool[0]['images']
        ['https://.../err.png']
        >>>
        >>> # 3. get_records directly reads linked Ticket #100 (without prior search) -> assigned '<reference_1>'
        >>> ticket_id = {'source_type': 'ticket', 'connection_id': None, 'record_id': 100}
        >>> pool.register_record(ticket_id, {'title': 'Internal Ticket 100', 'state': 'open'})
        '<reference_1>'
    """

    def __init__(self):
        self._sources: list[dict] = []
        self._index_by_key: dict[str, int] = {}
        self._excluded_keys: set[str] = set()
        self._featured_only_keys: set[str] = set()

    @staticmethod
    def source_key(source: dict) -> str:
        """Build the canonical hash key from a stored source dict."""
        return record_key(source['type'], source.get('connection_id'), source['_id'])

    @staticmethod
    def _format_label(index: int) -> str:
        return f'<reference_{index}>'

    def exclude(self, identity: dict) -> None:
        """Prevent ``identity`` from being registered as a citable source."""
        self._excluded_keys.add(
            record_key(identity['source_type'], identity['connection_id'], identity['record_id'])
        )

    def get_label(self, source_type, connection_id, record_id) -> str | None:
        """Return the ``<reference_i>`` label if already registered, else None."""
        index = self._index_by_key.get(record_key(source_type, connection_id, record_id))
        return self._format_label(index) if index is not None else None

    def register_search_result(
        self,
        search_result: dict,
        *,
        max_sources: int | None = None,
        inner_url: str = '',
    ) -> str | None:
        """Register a search hit or return its existing label; return None if excluded or ``max_sources`` is full."""
        key = self.source_key(search_result)
        if key in self._excluded_keys:
            return None
        existing_index = self._index_by_key.get(key)
        featured_only = key in self._featured_only_keys
        if existing_index is not None and not featured_only:
            if inner_url:
                self._sources[existing_index]['inner_url'] = inner_url
            return self._format_label(existing_index)

        if max_sources is not None and len(self._sources) - len(self._featured_only_keys) >= max_sources:
            return None

        if featured_only:
            source = self._sources[existing_index]
            source.update(search_result)
            if inner_url:
                source['inner_url'] = inner_url
            self._featured_only_keys.remove(key)
            return self._format_label(existing_index)

        if inner_url:
            search_result['inner_url'] = inner_url
        return self._append(key, search_result)

    def register_featured_record(self, identity: dict, metadata: dict) -> str | None:
        """Register a citable summary without consuming a search source slot."""
        key = record_key(**identity)
        if key in self._excluded_keys:
            return None
        existing_index = self._index_by_key.get(key)
        if existing_index is not None:
            return self._format_label(existing_index)

        source_type = identity['source_type']
        source = {field: metadata[field] for field in _FEATURED_SOURCE_METADATA_FIELDS if field in metadata}
        source.update({
            'type': source_type,
            '_id': identity['record_id'],
            'connection_id': source_type if ExtraSourceType.is_valid(source_type) else identity['connection_id'],
        })
        self._featured_only_keys.add(key)
        return self._append(key, source)

    def register_record(
        self,
        identity: dict,
        record: dict,
        *,
        image_urls: list[str] | None = None,
    ) -> str | None:
        """Register or enrich a full record read by ``get_records`` and return its ``<reference_i>`` label, or None if excluded."""
        source_type = identity['source_type']
        connection_id = identity['connection_id']
        record_id = identity['record_id']
        key = record_key(source_type, connection_id, record_id)
        if key in self._excluded_keys:
            return None

        existing_index = self._index_by_key.get(key)
        if existing_index is not None:
            self._featured_only_keys.discard(key)
            existing_source = self._sources[existing_index]
            for field_name in ('url', 'modified_time'):
                if record.get(field_name) is not None:
                    existing_source[field_name] = record[field_name]
            if image_urls:
                existing_source['images'] = image_urls
            return self._format_label(existing_index)

        source = {
            field_name: value
            for field_name, value in record.items()
            if field_name in _RECORD_SOURCE_METADATA_FIELDS
        }
        source.update({
            'type': source_type,
            '_id': record_id,
            'connection_id': (
                source_type if ExtraSourceType.is_valid(source_type)
                else connection_id
            ),
        })
        if image_urls:
            source['images'] = image_urls
        return self._append(key, source)

    def _append(self, key: str, source: dict) -> str:
        index = len(self._sources)
        self._sources.append(source)
        self._index_by_key[key] = index
        return self._format_label(index)

    def __len__(self) -> int:
        return len(self._sources)

    def __getitem__(self, index: int) -> dict:
        return self._sources[index]

    def __iter__(self):
        return iter(self._sources)
