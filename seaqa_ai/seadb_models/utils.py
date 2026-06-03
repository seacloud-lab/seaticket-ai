from seaqa_ai.utils.constants import ConnectionType, ExtraSourceType
from seaqa_ai.seadb_models.schema_loader import get_seadb_table_schemas
from seaqa_ai.seadb_models.models import SchemaTableNames


SCHEMA_TABLE_NAME_CONNECTION = {
    ConnectionType.GITHUB_ISSUE.value: SchemaTableNames.GITHUB_ISSUES,
    ConnectionType.DISCOURSE_FORUM.value: SchemaTableNames.DISCOURSE_TOPICS,
    ConnectionType.SITE.value: SchemaTableNames.WEB_CRAWL,
    ConnectionType.SEAFILE.value: SchemaTableNames.SEAFILE,
    ConnectionType.EMAIL.value: SchemaTableNames.THREAD,
    ConnectionType.GENERAL_TASK.value: SchemaTableNames.GENERAL_TASK,
}

# Mapping from connection_type to the comments/replies/secondary table
SCHEMA_TABLE_NAME_CONNECTION_COMMENTS = {
    ConnectionType.GITHUB_ISSUE.value: SchemaTableNames.GITHUB_ISSUE_COMMENTS,
    ConnectionType.DISCOURSE_FORUM.value: SchemaTableNames.DISCOURSE_REPLIES,
    ConnectionType.EMAIL.value: SchemaTableNames.EMAIL,
}

# Mapping from extra source types (no connection_id) to schema table keys
EXTRA_SOURCE_TABLE_MAP = {
    ExtraSourceType.KNOWLEDGE_BASE.value: SchemaTableNames.KNOWLEDGE_BASE,
    ExtraSourceType.TICKET.value: SchemaTableNames.TICKETS,
}
EXTRA_SOURCE_COMMENTS_MAP = {
    ExtraSourceType.TICKET.value: SchemaTableNames.TICKET_COMMENTS,
}


def _resolve_table_name_by_schema_table_key(schema_table_name, connection_id=None):
    if '{connection_id}' in schema_table_name:
        if connection_id is None:
            raise ValueError(f'connection_id is required for table name: {schema_table_name}')
        return schema_table_name.format(connection_id=connection_id)
    return schema_table_name


def get_table_name_from_schema(schema_table_name, connection_id=None):
    schema = get_seadb_table_schemas()
    tables = schema.get('tables') or {}
    table_schema = tables.get(schema_table_name) or {}
    table_name_schema = table_schema.get('table_name')
    if table_name_schema:
        return _resolve_table_name_by_schema_table_key(table_name_schema, connection_id)
    return ''


def get_column_name_from_schema(schema_table_name, column_name):
    """Return the column_name defined in YAML schema, or empty string if column not found. O(1) dict lookup."""
    schema = get_seadb_table_schemas()
    tables = schema.get('tables') or {}
    table_schema = tables.get(schema_table_name) or {}
    columns = table_schema.get('columns') or {}
    if column_name in columns:
        return column_name
    return ''


def get_connection_table_name(connection_type, connection_id=None):
    """Resolve the primary table name for a given connection_type and optional connection_id.

    Supports both ConnectionType values (site, seafile, github_issue, etc.) and
    ExtraSourceType values (knowledge_base, ticket).
    """
    # Check ExtraSourceType first (no connection_id needed)
    extra_table = EXTRA_SOURCE_TABLE_MAP.get(connection_type)
    if extra_table:
        return get_table_name_from_schema(extra_table)

    # Check ConnectionType
    schema_table_name = SCHEMA_TABLE_NAME_CONNECTION.get(connection_type)
    if not schema_table_name:
        return ''
    return get_table_name_from_schema(schema_table_name, connection_id=connection_id)


def get_connection_comments_table_name(connection_type, connection_id=None):
    """Resolve the comments/replies/secondary table name for a given connection_type."""
    # Check ExtraSourceType first
    extra_comments = EXTRA_SOURCE_COMMENTS_MAP.get(connection_type)
    if extra_comments:
        return get_table_name_from_schema(extra_comments)

    # Check ConnectionType
    schema_table_name = SCHEMA_TABLE_NAME_CONNECTION_COMMENTS.get(connection_type)
    if not schema_table_name:
        return None
    return get_table_name_from_schema(schema_table_name, connection_id=connection_id)
