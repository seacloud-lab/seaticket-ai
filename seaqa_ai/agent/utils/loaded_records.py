"""Records successfully loaded during one agent run."""

from seaqa_ai.agent.utils.record_identity import identity_key


class LoadedRecords:
    """Records successfully loaded during one agent run.

    Membership is an authorization check: phase filters and suggestion tools
    only accept record identities present here. A rebuilt run starts empty, so
    previously selected records must be loaded again before they are accepted.
    """

    def __init__(self):
        self._records = {}
        self.github_available_labels = {}

    @staticmethod
    def key(source_type, connection_id, record_id):
        return source_type, connection_id, record_id

    def add(self, source_type, connection_id, record_id, record):
        self._records[self.key(source_type, connection_id, record_id)] = record

    def get(self, source_type, connection_id, record_id):
        return self._records.get(self.key(source_type, connection_id, record_id))

    def contains(self, identity):
        key = identity_key(identity)
        return key is not None and key in self._records

    def find_by_source_id(self, source_type, source_id):
        """Resolve a trace-style source_id: '<connection_id>_<record_id>' or '<record_id>'."""
        for (loaded_source_type, connection_id, record_id), record in self._records.items():
            if loaded_source_type != source_type:
                continue
            loaded_source_id = (
                str(record_id) if connection_id is None else f'{connection_id}_{record_id}'
            )
            if loaded_source_id == source_id:
                return record
        return None
