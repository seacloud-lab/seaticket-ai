import logging
from seaqa_ai.agent.tools import BasicTools

logger = logging.getLogger(__name__)

class ContentGenerators(BasicTools):
    tools = {
        'markdown_generator': {
            'type': 'function',
            'function': {
                'name': 'markdown_generator',
                'description': "This tool will be used to generate Markdown files (or documents) by processing requests for Markdown files (or documents) generation. The final result of this tool is a series of enclosed HTML tags (`<seaqa-markdown>`).",
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
    }

    def _initialize_tool_name_func_map(self):
        self.tool_name_func_map = {
            'markdown_generator': self.markdown_generator
        }
    
    def markdown_generator(self, file_name, content):
        """
        generate the md files with <seaqa-markdown> HTML tag
        """

        return f"""<seaqa-markdown file_name="{file_name}">
{content}
</seaqa-markdown>
"""
