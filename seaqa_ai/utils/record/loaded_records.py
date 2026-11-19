"""One request's shared record data cache and authorization membership."""

from seaqa_ai.utils.record.identity import identity_key
from seaqa_ai.utils import gen_record_hash


class LoadedRecords:
    """Records successfully loaded during one Chat request or Agent run.

    Membership is an authorization check: phase filters and suggestion tools
    only accept record identities present here. A rebuilt run starts empty, so
    previously selected records must be loaded again before they are accepted.
    """

    def __init__(self):
        # record_key -> {'identity': dict, 'record': dict}
        self._records: dict[str, dict] = {}

    @staticmethod
    def key(source_type, connection_id, record_id):
        return gen_record_hash(source_type, connection_id, record_id)

    def add(self, source_type, connection_id, record_id, record):
        key = self.key(source_type, connection_id, record_id)
        self._records[key] = {
            'identity': {
                'source_type': source_type, 'connection_id': connection_id, 'record_id': record_id,
            },
            'record': record,
        }

    def get(self, source_type, connection_id, record_id):
        entry = self._records.get(self.key(source_type, connection_id, record_id))
        return entry['record'] if entry else None

    def contains(self, identity):
        key = identity_key(identity)
        return key is not None and self.key(*key) in self._records

    def find_by_source_id(self, source_type, source_id):
        """Resolve a trace-style source_id: '<connection_id>_<record_id>' or '<record_id>'."""
        for entry in self._records.values():
            identity = entry['identity']
            loaded_source_type = identity['source_type']
            connection_id = identity['connection_id']
            record_id = identity['record_id']
            if loaded_source_type != source_type:
                continue
            loaded_source_id = (
                str(record_id) if connection_id is None else f'{connection_id}_{record_id}'
            )
            if loaded_source_id == source_id:
                return entry['record']
        return None
