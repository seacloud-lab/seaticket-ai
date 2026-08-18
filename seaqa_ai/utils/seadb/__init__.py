from ._seadb_query_service import SeaDBQueryService
from ._data_assembler import DataAssembler
from ._seadb_api import SeaDBAPI


class SeaDBService:
    """Application-facing SeaDB entry point for transport and record retrieval."""

    def __init__(self, server_url, seadb_user, seadb_passwd, timeout=30):
        self._api = SeaDBAPI(server_url, seadb_user, seadb_passwd, timeout=timeout)
        self._query_service = SeaDBQueryService(self._api)

    def ping(self):
        return self._api.ping()

    def query_rows(self, base_id, sql, params=None, convert_keys=True):
        return self._api.query_rows(base_id, sql, params=params, convert_keys=convert_keys)

    def insert_rows(self, base_id, table_name, rows):
        return self._api.insert_rows(base_id, table_name, rows)

    def update_rows(self, base_id, table_name, rows):
        return self._api.update_rows(base_id, table_name, rows)

    def get_base_metadata(self, base_id):
        return self._api.get_base_metadata(base_id)

    def get_records_by_pks(self, base_id, table_name, pks, fields_str='*'):
        return self._query_service.get_records_by_pks(base_id, table_name, pks, fields_str)

    def get_selected_topic_entries(self, base_id, table_name, topic_ids, topic_id_field_name,
                                   fields_str='*', has_deleted_field=True, limit_for_each_topic=20,
                                   first_order_by_field=None, latest_order_by_field=None,
                                   dedupe_key_fields=None, sort_key_fields=None,
                                   initial_content_lengths=None, max_total_content_size=None,
                                   content_field_name='content', more_omitted_notice_builder=None,
                                   prioritize_latest_entry=False):
        topic_ids = list(topic_ids or [])
        if not topic_ids:
            return {}

        required_fields = [
            topic_id_field_name, content_field_name, first_order_by_field,
            latest_order_by_field, *(dedupe_key_fields or []), *(sort_key_fields or []),
        ]
        fields_str = self._query_service._normalize_fields_str(fields_str, required_fields)
        selection_kwargs = {
            'limit_for_each_topic': limit_for_each_topic,
            'dedupe_key_fields': dedupe_key_fields,
            'sort_key_fields': sort_key_fields,
            'first_order_by_field': first_order_by_field,
            'initial_content_lengths': initial_content_lengths,
            'max_total_content_size': max_total_content_size,
            'content_field_name': content_field_name,
            'more_omitted_notice_builder': more_omitted_notice_builder,
            'prioritize_latest_entry': prioritize_latest_entry,
        }
        if first_order_by_field == latest_order_by_field:
            grouped_rows = self._query_service.get_topic_entries_grouped(
                base_id, table_name, topic_ids, topic_id_field_name, fields_str,
                has_deleted_field, [first_order_by_field] if first_order_by_field else None,
            )
            return DataAssembler.assemble_from_ordered_rows(
                topic_ids, grouped_rows, **selection_kwargs,
            )

        first_rows = self._query_service.get_topic_entries_grouped(
            base_id, table_name, topic_ids, topic_id_field_name, fields_str,
            has_deleted_field, [first_order_by_field] if first_order_by_field else None,
        )
        latest_rows = self._query_service.get_topic_entries_grouped(
            base_id, table_name, topic_ids, topic_id_field_name, fields_str,
            has_deleted_field, [latest_order_by_field] if latest_order_by_field else None,
        )
        latest_limit = limit_for_each_topic if limit_for_each_topic <= 1 else limit_for_each_topic - 1
        return DataAssembler.assemble(
            topic_ids,
            {topic_id: rows[0] for topic_id, rows in first_rows.items() if rows},
            {topic_id: rows[-latest_limit:] if latest_limit > 0 else [] for topic_id, rows in latest_rows.items()},
            **selection_kwargs,
        )


__all__ = ['SeaDBService']
