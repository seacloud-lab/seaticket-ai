class DataAssembler:
    """Select and budget already-loaded topic entries without data-source knowledge."""

    @classmethod
    def assemble(cls, topic_ids, first_rows_by_topic, latest_rows_by_topic,
               limit_for_each_topic=20, dedupe_key_fields=None, sort_key_fields=None,
               first_order_by_field=None, initial_content_lengths=None,
               max_total_content_size=None, content_field_name='content',
               more_omitted_notice_builder=None, prioritize_latest_entry=False):
        dedupe_key_fields = list(dedupe_key_fields or [])
        sort_key_fields = list(sort_key_fields or dedupe_key_fields or [])
        initial_content_lengths = initial_content_lengths or {}
        result = {}

        for topic_id in topic_ids:
            entries = cls._merge_entries(
                first_rows_by_topic.get(topic_id),
                latest_rows_by_topic.get(topic_id, []),
                dedupe_key_fields,
                sort_key_fields,
            )
            entries = cls._prioritize_latest(
                entries, first_order_by_field, sort_key_fields, limit_for_each_topic,
            )
            if max_total_content_size is None:
                result[topic_id] = cls._sort(entries, sort_key_fields)
                continue
            result[topic_id] = cls._apply_content_budget(
                entries=entries,
                initial_content_length=int(initial_content_lengths.get(topic_id, 0) or 0),
                max_total_content_size=max_total_content_size,
                content_field_name=content_field_name,
                sort_key_fields=sort_key_fields,
                more_omitted_notice_builder=more_omitted_notice_builder,
                prioritize_latest_entry=prioritize_latest_entry,
            )
        return result

    @classmethod
    def assemble_from_ordered_rows(cls, topic_ids, grouped_rows, limit_for_each_topic=20,
                                 dedupe_key_fields=None, sort_key_fields=None,
                                 first_order_by_field=None, initial_content_lengths=None,
                                 max_total_content_size=None, content_field_name='content',
                                 more_omitted_notice_builder=None, prioritize_latest_entry=False):
        latest_limit = limit_for_each_topic if limit_for_each_topic <= 1 else limit_for_each_topic - 1
        first_rows = {}
        latest_rows = {}
        for topic_id in topic_ids:
            rows = grouped_rows.get(topic_id, [])
            if rows:
                first_rows[topic_id] = rows[0]
            latest_rows[topic_id] = rows[-latest_limit:] if latest_limit > 0 else []
        return cls.assemble(
            topic_ids, first_rows, latest_rows, limit_for_each_topic, dedupe_key_fields,
            sort_key_fields, first_order_by_field, initial_content_lengths,
            max_total_content_size, content_field_name, more_omitted_notice_builder,
            prioritize_latest_entry,
        )

    @classmethod
    def _merge_entries(cls, first_entry, latest_entries, dedupe_key_fields, sort_key_fields):
        entries_by_key = {}
        for entry in [*latest_entries, first_entry]:
            if not entry:
                continue
            key = tuple(entry.get(field) for field in dedupe_key_fields) if dedupe_key_fields else id(entry)
            entries_by_key[key] = entry
        return cls._sort(entries_by_key.values(), sort_key_fields)

    @classmethod
    def _prioritize_latest(cls, entries, first_order_by_field, sort_key_fields, limit):
        if limit <= 0 or not entries:
            return []
        newest_first = sorted(entries, key=cls._sort_key(sort_key_fields), reverse=True)
        if limit == 1:
            return newest_first[:1]
        first_entry = min(entries, key=lambda entry: entry.get(first_order_by_field) or '') if first_order_by_field else entries[0]
        latest_entry = newest_first[0]
        if latest_entry is first_entry:
            return newest_first[:limit]
        return [latest_entry, first_entry] + [
            entry for entry in newest_first
            if entry is not latest_entry and entry is not first_entry
        ][:limit - 2]

    @classmethod
    def _apply_content_budget(cls, entries, initial_content_length, max_total_content_size,
                              content_field_name, sort_key_fields, more_omitted_notice_builder,
                              prioritize_latest_entry):
        if not entries:
            return []
        selected_entries = []
        omitted_notice = None
        latest_entry = entries[0]
        latest_content = latest_entry.get(content_field_name) or ''

        if prioritize_latest_entry:
            if len(latest_content) > max_total_content_size:
                truncated_entry = dict(latest_entry)
                truncated_entry[content_field_name] = cls._truncate(latest_content, max_total_content_size)
                return cls._sort([truncated_entry], sort_key_fields)
            selected_entries.append(latest_entry)
            total_content_size = len(latest_content) + min(
                initial_content_length, max_total_content_size - len(latest_content),
            )
            entries_to_add = entries[1:]
        else:
            if initial_content_length >= max_total_content_size:
                return []
            total_content_size = initial_content_length
            entries_to_add = entries

        for entry in entries_to_add:
            content = entry.get(content_field_name) or ''
            remaining_size = max_total_content_size - total_content_size
            if remaining_size <= 0:
                omitted_notice = more_omitted_notice_builder() if more_omitted_notice_builder else None
                break
            if len(content) > remaining_size:
                truncated_content = cls._truncate(content, remaining_size)
                if truncated_content:
                    truncated_entry = dict(entry)
                    truncated_entry[content_field_name] = truncated_content
                    selected_entries.append(truncated_entry)
                omitted_notice = more_omitted_notice_builder() if more_omitted_notice_builder else None
                break
            selected_entries.append(entry)
            total_content_size += len(content)

        result = cls._sort(selected_entries, sort_key_fields)
        if omitted_notice:
            result.append(omitted_notice)
        return result

    @staticmethod
    def _truncate(content, max_length):
        if max_length <= 0:
            return ''
        if len(content) <= max_length:
            return content
        if max_length <= 3:
            return '.' * max_length
        return content[:max_length - 3] + '...'

    @staticmethod
    def _sort_key(fields):
        return lambda entry: tuple(entry.get(field) or '' for field in fields)

    @classmethod
    def _sort(cls, entries, sort_key_fields):
        return sorted(entries, key=cls._sort_key(sort_key_fields))
