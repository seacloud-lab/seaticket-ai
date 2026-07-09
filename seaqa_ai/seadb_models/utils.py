from seaqa_ai.utils.constants import ConnectionType, ExtraSourceType
from seaqa_ai.seadb_models.models import SchemaTables


SCHEMA_TABLE_CONNECTION = {
    ConnectionType.GITHUB_ISSUE.value: SchemaTables.GITHUB_ISSUES,
    ConnectionType.DISCOURSE_FORUM.value: SchemaTables.DISCOURSE_TOPICS,
    ConnectionType.SITE.value: SchemaTables.WEB_CRAWL,
    ConnectionType.SEAFILE.value: SchemaTables.SEAFILE,
    ConnectionType.EMAIL.value: SchemaTables.THREAD,
    ConnectionType.GENERAL_TASK.value: SchemaTables.GENERAL_TASK,
    ConnectionType.LINEAR.value: SchemaTables.LINEAR_ISSUES,
}

# Mapping from extra source types (no connection_id) to schema table keys
EXTRA_SOURCE_TABLE_MAP = {
    ExtraSourceType.KNOWLEDGE_BASE.value: SchemaTables.KNOWLEDGE_BASE,
    ExtraSourceType.TICKET.value: SchemaTables.TICKETS,
}


def get_connection_table_name(connection_type, connection_id=None):
    """Resolve the primary table name for a given connection_type and optional connection_id.

    Supports both ConnectionType values (site, seafile, github_issue, etc.) and
    ExtraSourceType values (knowledge_base, ticket).
    """
    # Check ExtraSourceType first (no connection_id needed)
    extra_table = EXTRA_SOURCE_TABLE_MAP.get(connection_type)
    if extra_table:
        return extra_table.table_name()

    # Check ConnectionType
    connection_schema_table = SCHEMA_TABLE_CONNECTION.get(connection_type)
    if not connection_schema_table:
        return ''
    return connection_schema_table.table_name(connection_id)
