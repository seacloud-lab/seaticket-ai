import time
import jwt
import logging
import requests
from seaqa_ai.utils import uuid_str_to_36_chars

logger = logging.getLogger(__name__)
SEADB_COMPONENT_USERNAME = 'seaqa-ai'


def _truncate_content_with_ellipsis(content, max_length):
    content = content or ''
    if max_length <= 0:
        return ''
    if len(content) <= max_length:
        return content
    if max_length <= 3:
        return '.' * max_length
    return content[:max_length - 3] + '...'

def parse_response(response):
    if response.status_code >= 400 or response.status_code < 200:
        raise ConnectionError(response.status_code, response.text)
    else:
        try:
            return response.json()
        except:
            pass



class SeaDBAPI:
    def __init__(self, secret_key, server_url, timeout=30, username=SEADB_COMPONENT_USERNAME):
        self.timeout = timeout
        self.secret_key = secret_key
        self.server_url = server_url
        self.username = username

    def gen_headers(self, base_id):
        payload = {
            'exp': int(time.time()) + 3600,
            'base_id': base_id,
            'username': self.username,
        }
        token = jwt.encode(payload, self.secret_key, algorithm='HS256')
        return {"Authorization": "Bearer %s" % token}

    def ping(self):
        url = f'{self.server_url}/ping'
        response = requests.get(url, timeout=self.timeout)
        return parse_response(response)
    
    def query_rows(self, base_id, sql, params=[], convert_keys=True):
        base_id = uuid_str_to_36_chars(base_id)
        headers = self.gen_headers(base_id)
        post_data = {
            'sql': sql,
            'convert_keys': convert_keys
        }
        if params:
            post_data['params'] = params
        url = f'{self.server_url}/api/v1/{base_id}/query'
        response = requests.post(url, json=post_data, headers=headers, timeout=self.timeout)
        return parse_response(response)
    
    def insert_rows(self, base_id, table_name, rows):
        base_id = uuid_str_to_36_chars(base_id)
        headers = self.gen_headers(base_id)
        url = f'{self.server_url}/api/v1/{base_id}/rows'
        data = {
            'table_name': str(table_name),
            'rows': rows
        }
        response = requests.post(url, json=data, headers=headers, timeout=self.timeout)
        return parse_response(response)

    def update_rows(self, base_id, table_name, rows):
        base_id = uuid_str_to_36_chars(base_id)
        headers = self.gen_headers(base_id)
        url = f'{self.server_url}/api/v1/{base_id}/rows'
        data = {
            'table_name': table_name,
            'updates': rows
        }
        response = requests.put(url, json=data, headers=headers, timeout=self.timeout)
        return parse_response(response)

    def get_base_metadata(self, base_id):
        base_id = uuid_str_to_36_chars(base_id)
        headers = self.gen_headers(base_id)
        url = f'{self.server_url}/api/v1/{base_id}/metadata'
        response = requests.get(url, headers=headers, timeout=self.timeout)
        return parse_response(response)

    def get_records_by_pks(self, base_id, table_name, pks, fields_str='*'):
        """
        return {
            'pk': record
        }
        """
        if fields_str != '*':
            if '_pk' not in fields_str:
                fields_str = '_pk, ' + fields_str

        condition_str = ", ".join([
            str(pk) if not isinstance(pk, str) else f'"{pk}"'
            for pk in pks
        ])

        records = self.query_rows(
            base_id, 
            f'SELECT {fields_str} FROM {table_name} WHERE _pk IN ({condition_str}) AND (`deleted` = False OR `deleted` IS NULL)'
        )['results']

        return {
            record['_pk']: record
            for record in records
        }

    def _normalize_fields_str(self, fields_str, required_fields):
        if fields_str == '*':
            return fields_str

        normalized_fields = []
        seen = set()
        for field in fields_str.split(','):
            normalized_field = field.strip()
            if not normalized_field or normalized_field in seen:
                continue
            seen.add(normalized_field)
            normalized_fields.append(normalized_field)

        for field in required_fields:
            normalized_field = str(field or '').strip()
            if not normalized_field or normalized_field in seen:
                continue
            seen.add(normalized_field)
            normalized_fields.append(normalized_field)

        return ', '.join(normalized_fields)

    def _build_topic_entry_sql(self, table_name, topic_id, topic_id_field_name, fields_str='*',
                               has_deleted_field=True, order_by_field=None, descending=False, limit=None):
        sql = f'SELECT {fields_str} FROM {table_name} WHERE {topic_id_field_name} = '
        sql += str(topic_id) if not isinstance(topic_id, str) else f'"{topic_id}"'

        if has_deleted_field:
            sql += ' AND (`deleted` = False OR `deleted` IS NULL)'

        if order_by_field:
            order_direction = 'DESC' if descending else 'ASC'
            sql += f' ORDER BY `{order_by_field}` {order_direction}'

        if limit is not None:
            sql += f' LIMIT {int(limit)}'

        return sql

    def _make_record_key(self, record, key_fields):
        if not key_fields:
            return None
        return tuple(record.get(field) for field in key_fields)

    def get_first_topic_entries(self, base_id, table_name, topic_ids, topic_id_field_name,
                                fields_str='*', has_deleted_field=True, order_by_field=None):
        topic_ids = list(topic_ids or [])
        if not topic_ids:
            return {}

        fields_str = self._normalize_fields_str(fields_str, [topic_id_field_name, order_by_field])

        result = {}
        for topic_id in topic_ids:
            sql = self._build_topic_entry_sql(
                table_name=table_name,
                topic_id=topic_id,
                topic_id_field_name=topic_id_field_name,
                fields_str=fields_str,
                has_deleted_field=has_deleted_field,
                order_by_field=order_by_field,
                descending=False,
                limit=1,
            )
            rows = self.query_rows(base_id, sql).get('results', [])
            if rows:
                result[topic_id] = rows[0]
        return result

    def get_latest_topic_entries(self, base_id, table_name, topic_ids, topic_id_field_name,
                                 fields_str='*', has_deleted_field=True, order_by_field=None,
                                 limit_for_each_topic=20):
        topic_ids = list(topic_ids or [])
        if not topic_ids:
            return {}

        fields_str = self._normalize_fields_str(fields_str, [topic_id_field_name, order_by_field])

        result = {}
        for topic_id in topic_ids:
            sql = self._build_topic_entry_sql(
                table_name=table_name,
                topic_id=topic_id,
                topic_id_field_name=topic_id_field_name,
                fields_str=fields_str,
                has_deleted_field=has_deleted_field,
                order_by_field=order_by_field,
                descending=True,
                limit=limit_for_each_topic,
            )
            result[topic_id] = self.query_rows(base_id, sql).get('results', [])
        return result

    def get_selected_topic_entries(self, base_id, table_name, topic_ids, topic_id_field_name,
                                   fields_str='*', has_deleted_field=True, limit_for_each_topic=20,
                                   first_order_by_field=None, latest_order_by_field=None,
                                   dedupe_key_fields=None, sort_key_fields=None,
                                   initial_content_lengths=None, max_total_content_size=None,
                                   content_field_name='content', omitted_notice_builder=None,
                                   more_omitted_notice_builder=None):
        """
        return {
            topic_id: [entries]
        }
        """
        topic_ids = list(topic_ids or [])
        if not topic_ids:
            return {}

        dedupe_key_fields = list(dedupe_key_fields or [])
        sort_key_fields = list(sort_key_fields or dedupe_key_fields or [])
        initial_content_lengths = initial_content_lengths or {}
        required_fields = [
            topic_id_field_name,
            content_field_name,
            first_order_by_field,
            latest_order_by_field,
            *dedupe_key_fields,
            *sort_key_fields,
        ]
        fields_str = self._normalize_fields_str(fields_str, required_fields)

        first_entries = self.get_first_topic_entries(
            base_id=base_id,
            table_name=table_name,
            topic_ids=topic_ids,
            topic_id_field_name=topic_id_field_name,
            fields_str=fields_str,
            has_deleted_field=has_deleted_field,
            order_by_field=first_order_by_field,
        )

        result = {}
        latest_query_topic_ids = []
        for topic_id in topic_ids:
            initial_content_length = int(initial_content_lengths.get(topic_id, 0) or 0)
            if max_total_content_size is not None and initial_content_length >= max_total_content_size:
                result[topic_id] = [omitted_notice_builder()] if omitted_notice_builder else []
                continue

            first_entry = first_entries.get(topic_id)
            if not first_entry:
                result[topic_id] = []
                continue

            remaining_size = None if max_total_content_size is None else max_total_content_size - initial_content_length
            first_content = first_entry.get(content_field_name) or ''
            if remaining_size is not None and len(first_content) > remaining_size:
                selected_entries = []
                truncated_content = _truncate_content_with_ellipsis(first_content, remaining_size)
                if truncated_content:
                    truncated_entry = dict(first_entry)
                    truncated_entry[content_field_name] = truncated_content
                    selected_entries.append(truncated_entry)
                if more_omitted_notice_builder:
                    selected_entries.append(more_omitted_notice_builder())
                result[topic_id] = selected_entries
                continue

            if limit_for_each_topic <= 1:
                result[topic_id] = [first_entry]
                continue

            latest_query_topic_ids.append(topic_id)

        latest_entries_map = self.get_latest_topic_entries(
            base_id=base_id,
            table_name=table_name,
            topic_ids=latest_query_topic_ids,
            topic_id_field_name=topic_id_field_name,
            fields_str=fields_str,
            has_deleted_field=has_deleted_field,
            order_by_field=latest_order_by_field,
            limit_for_each_topic=limit_for_each_topic,
        )

        for topic_id in latest_query_topic_ids:
            first_entry = first_entries.get(topic_id)
            latest_entries = latest_entries_map.get(topic_id, [])
            entries_by_key = {}

            for entry in latest_entries:
                record_key = self._make_record_key(entry, dedupe_key_fields)
                if record_key is None:
                    record_key = id(entry)
                entries_by_key[record_key] = entry

            first_entry_key = self._make_record_key(first_entry, dedupe_key_fields)
            if first_entry_key is None:
                first_entry_key = id(first_entry)
            entries_by_key[first_entry_key] = first_entry

            entries = list(entries_by_key.values())
            if sort_key_fields:
                entries = sorted(
                    entries,
                    key=lambda entry: tuple(entry.get(field) or '' for field in sort_key_fields),
                )

            if max_total_content_size is None:
                result[topic_id] = entries
                continue

            selected_entries = []
            total_content_size = int(initial_content_lengths.get(topic_id, 0) or 0)
            for entry in entries:
                content = entry.get(content_field_name) or ''
                remaining_size = max_total_content_size - total_content_size
                if remaining_size <= 0:
                    break

                if len(content) > remaining_size:
                    truncated_content = _truncate_content_with_ellipsis(content, remaining_size)
                    if truncated_content:
                        truncated_entry = dict(entry)
                        truncated_entry[content_field_name] = truncated_content
                        selected_entries.append(truncated_entry)
                    if more_omitted_notice_builder:
                        selected_entries.append(more_omitted_notice_builder())
                    break

                selected_entries.append(entry)
                total_content_size += len(content)

            result[topic_id] = selected_entries

        return result
