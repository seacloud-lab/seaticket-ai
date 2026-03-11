import logging
from seaqa_ai.utils.tools import BasicTool

logger = logging.getLogger(__name__)

class GenerateMarkdown(BasicTool):
    tool = {
        'type': 'function',
        'function': {
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
                'required': ['file_name', 'content']
            }
        }
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
        'function': {
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
                'required': ['title', 'content']
            }
        }
    }

    def execute(self, title, content):
        """
        generate the kb entry with <seaqa-kb-entry> HTML tag (content in tag body, like GenerateMarkdown)
        """
        return f"""<seaqa-kb-entry title="{title}">
{content}
</seaqa-kb-entry>
"""
