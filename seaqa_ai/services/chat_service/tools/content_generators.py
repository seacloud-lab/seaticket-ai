import datetime
import logging
from html import escape

from seaqa_ai import config
from seaqa_ai.utils.seadb_api import SeaDBAPI
from seaqa_ai.utils.tools import BasicTool
from seaqa_ai.seadb_models import SchemaTables

logger = logging.getLogger(__name__)

class MarkdownGenerator(BasicTool):
    tool = {
        'type': 'function',
        'name': 'generate_markdown',
        'description': "This tool processes requests to generate Markdown files or documents. The output is returned as content wrapped within `<seaqa-markdown>` HTML tags.",
        'parameters':{
            'type': 'object',
            'properties': {
                'file_name': {
                    'type': 'string',
                    'description': """This parameter represents the filename of the generated Markdown file (or document), which must follow these rules:
- Must end with .md
- Use lowercase letters, numbers, hyphens, or underscores
- Should reflect the document content"""
                },
                'content': {
                    'type': 'string',
                    'description': """This parameter represents the content of the generated Markdown file (or document). This parameter of should follow the Markdown's standard format.

Here is an example:
# Seafile Docker overview
Seafile docker based installation consist of the following components (docker images):
- Seafile server: Seafile core services, see [Seafile Components](https://manual.seafile.com/latest/introduction/components.md) for the details.
- MySQL: Database stores data related to Seafile and SeaDoc.
- Redis: Cache server.
- Caddy: Caddy server enables user to access the Seafile service (i.e., Seafile server and Sdoc server) externally and handles `SSL` configuration.
    """
                }
            },
            'required': ['file_name', 'content'],
            'additionalProperties': False,
        },
        'strict': True,
    }

    def execute(self, file_name, content):
        """
        generate the md files with <seaqa-markdown> HTML tag
        """

        return f"""<seaqa-markdown file_name="{file_name}">
{content}
</seaqa-markdown>
"""


class KnowledgeBaseEntryCreator(BasicTool):
    tool = {
        'type': 'function',
        'name': 'create_knowledge_base_entry',
        'description': """This tool creates a knowledge base entry based on the conversation context. Use this tool when the user explicitly requests to create, add, or save a knowledge base entry/record from the current chat content. The output is returned as content wrapped within `<seaqa-kb-entry>` HTML tags, which will be processed by the system to automatically create the record.""",
        'parameters': {
            'type': 'object',
            'properties': {
                'title': {
                    'type': 'string',
                    'description': 'A concise, descriptive title for the knowledge base entry. Should accurately summarize the main topic. Maximum 200 characters.'
                },
                'content': {
                    'type': 'string',
                    'description': """The main content of the knowledge base entry. Please don't use '\\n' to separate paragraphs, use Markdown's standard format instead.

Here is an example:
# Issue: Cannot access Seafile after upgrade

## Problem Description
After upgrading Seafile from 9.0 to 10.0, users cannot log in via LDAP.

## Root Cause
The LDAP configuration format changed in version 10.0.

## Solution
Update `seahub_settings.py` with the new LDAP format:
```python
LDAP_SERVER_URL = 'ldap://your-server'
```
"""
                }
            },
            'required': ['title', 'content'],
            'additionalProperties': False,
        },
        'strict': True,
    }

    def execute(self, title, content, context):
        """
        Create KB record and return <seaqa-kb-entry> HTML tag with title and pk.
        """
        now = datetime.datetime.now(datetime.timezone.utc).isoformat()
        seadb_api = SeaDBAPI(
            config.SEADB_SERVER_URL,
            config.SEADB_USER,
            config.SEADB_PASSWORD
        )

        row = {
            'title': title,
            'content': content,
            'tags': [],
            'creator': context['username'],
            'created_time': now,
            'last_modifier': context['username'],
            'modified_time': now,
            'deleted': False,
        }
        res = seadb_api.insert_rows(context['project_uuid'], SchemaTables.KNOWLEDGE_BASE.table_name(), [row])
        pks = res.get('pks', [])
        if len(pks) != 1:
            raise AssertionError('Failed to create knowledge base entry')

        kb_pk = pks[0]
        return f'<seaqa-kb-entry id="{kb_pk}" title="{escape(title, quote=True)}" />'


class TicketCreator(BasicTool):
    tool = {
        'type': 'function',
        'name': 'create_ticket',
        'description': """This tool creates an internal ticket from the current chat content. Use this tool only when the user explicitly requests to create, open, or file a ticket. The output is returned as content wrapped within `<seaqa-ticket>` HTML tags, which will be processed by the system to generate a clickable ticket link.""",
        'parameters': {
            'type': 'object',
            'properties': {
                'title': {
                    'type': 'string',
                    'description': 'Ticket title. Keep it concise and specific.'
                },
                'content': {
                    'type': 'string',
                    'description': 'Ticket description in Markdown format. Keep sections and bullet points readable.'
                },
                'type': {
                    'type': 'string',
                    'description': 'Optional ticket type name, such as Bug, Feature, or Question. If omitted, it will be empty.'
                },
                'priority': {
                    'type': 'integer',
                    'description': 'Optional ticket priority from 0 to 5. If omitted, defaults to 0 (No priority). 1 means urgent; 2 means high; 3 means medium; 4 means low.'
                },
                'state': {
                    'type': 'string',
                    'description': 'Ticket state, typically open'
                },
            },
            'required': ['title', 'content', 'state'],
            'additionalProperties': False,
        },
        'strict': True,
    }

    def execute(self, title, content, state, context, type='', priority=0):
        """
        Create ticket and return <seaqa-ticket> HTML tag with title and pk.
        """
        title = str(title or '').strip()
        content = str(content or '').strip()
        type_name = str(type or '').strip()
        state_name = str(state or '').strip().lower()

        if not title:
            raise AssertionError('Ticket title is required')
        if not content:
            raise AssertionError('Ticket content is required')
        try:
            priority_value = int(priority)
        except Exception as e:
            raise AssertionError('Ticket priority must be an integer') from e
        if priority_value < 0:
            priority_value = 0
        elif priority_value > 5:
            priority_value = 5

        if not state_name:
            state_name = 'open'

        now = datetime.datetime.now(datetime.timezone.utc).isoformat()
        seadb_api = SeaDBAPI(
            config.SEADB_SERVER_URL,
            config.SEADB_USER,
            config.SEADB_PASSWORD
        )

        row = {
            'title': title,
            'content': content,
            'state': state_name,
            'substate': 'New' if state_name == 'open' else '',
            'type': type_name,
            'tags': [],
            'assignees': [],
            'participants': [context['username']],
            'linked_connection_records': [],
            'priority': priority_value,
            'creator': context['username'],
            'comment_count': 0,
            'created_time': now,
            'modified_time': now,
            'deleted': False,
            'due_date': '',
        }
        res = seadb_api.insert_rows(context['project_uuid'], SchemaTables.TICKETS.table_name(), [row])
        pks = res.get('pks', [])
        if len(pks) != 1:
            raise AssertionError('Failed to create ticket')
        ticket_id = pks[0]

        return f'<seaqa-ticket id="{ticket_id}" title="{escape(title, quote=True)}" />'
