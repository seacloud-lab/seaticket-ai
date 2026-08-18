class SeaDBQueryService:
    """SeaDB-specific query construction and row retrieval."""

    def __init__(self, api):
        self._api = api

    def get_records_by_pks(self, base_id, table_name, pks, fields_str='*'):
        pks = list(pks or [])
        if not pks:
            return {}
        fields_str = self._normalize_fields_str(fields_str, ['_pk'])
        condition_str = self._build_in_condition(pks)
        rows = self._api.query_rows(
            base_id,
            f'SELECT {fields_str} FROM {table_name} WHERE _pk IN ({condition_str}) '
            'AND (`deleted` = False OR `deleted` IS NULL)',
        ).get('results', [])
        return {row['_pk']: row for row in rows}

    def get_topic_entries_grouped(self, base_id, table_name, topic_ids, topic_id_field_name,
                                  fields_str='*', has_deleted_field=True, order_by_fields=None):
        topic_ids = list(topic_ids or [])
        if not topic_ids:
            return {}

        sql = self._build_topic_entries_sql(
            table_name=table_name,
            topic_ids=topic_ids,
            topic_id_field_name=topic_id_field_name,
            fields_str=fields_str,
            has_deleted_field=has_deleted_field,
            order_by_fields=[topic_id_field_name, *(order_by_fields or [])],
        )
        rows = self._api.query_rows(base_id, sql).get('results', [])
        grouped_rows = {}
        for row in rows:
            grouped_rows.setdefault(row.get(topic_id_field_name), []).append(row)
        return grouped_rows

    @staticmethod
    def _normalize_fields_str(fields_str, required_fields):
        if fields_str == '*':
            return fields_str

        fields = []
        seen = set()
        for field in [*fields_str.split(','), *required_fields]:
            field = str(field or '').strip()
            if field and field not in seen:
                seen.add(field)
                fields.append(field)
        return ', '.join(fields)

    @staticmethod
    def _build_in_condition(values):
        return ', '.join(
            str(value) if not isinstance(value, str) else f'"{value}"'
            for value in values
        )

    @classmethod
    def _build_topic_entries_sql(cls, table_name, topic_ids, topic_id_field_name, fields_str='*',
                                 has_deleted_field=True, order_by_fields=None):
        sql = (
            f'SELECT {fields_str} FROM {table_name} WHERE {topic_id_field_name} '
            f'IN ({cls._build_in_condition(topic_ids)})'
        )
        if has_deleted_field:
            sql += ' AND (`deleted` = False OR `deleted` IS NULL)'

        order_by_fields = [field for field in (order_by_fields or []) if field]
        if order_by_fields:
            sql += ' ORDER BY ' + ', '.join(f'`{field}` ASC' for field in order_by_fields)
        return sql
