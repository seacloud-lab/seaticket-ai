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
                            'description': """This parameter represents the content of the generated Markdown file (or document). This parameter of should follow the Markdown's format:
1. Headings 
    - Use # for main headings (H1) 
    - Use ## for subheadings (H2) 
    - Continue with ### (H3), #### (H4), etc. for nested levels
    - Keep headings concise and descriptive
2. Lists
    2.1 Unordered Lists
        - Use - or * for bullet points
        - Indent sub-items with two spaces
    2.2 Ordered Lists
        Use 1., 2., etc. for numbered lists
3. Text Formatting
    - Bold: **text** or __text__
    - Italic: *text* or _text_
    - Strikethrough: ~~text~~
    - Inline code: `code`
4. Links
    - Basic link: [Link Text](URL)
    - Add title (tooltip): [Link Text](URL "Title")
    - Reference-style links:
        - 1[Link Text][ref]
        - 2[ref]: URL "Title"
5. Images
    - Basic image: ![Alt_Text](image-url)
    - With title: ![Alt_Text](image-url "Title")
    - Reference-style:
        - 1![Alt_Text][img-ref]
        - 2[img-ref]: image-url "Title"
6. Code Blocks
    - Inline Code: Use backticks: `function()`
    - Multi-line Code Blocks: Indent with 4 spaces or use triple backticks:
        ```language
        code here
        ```
        (Replace "language" with actual language like python, javascript, etc.)
7. Use > for blockquotes
8.Horizontal Rules (Use three or more of):
    - Hyphens ---
    - Asterisks ***
    - Underscores ___
9. Tables (Use pipes | and hyphens -)
    Example:
    1| Header 1 | Header 2 |
    2|----------|----------|
    3| Row 1 | Data 1 |
    4| Row 2 | Data 2 |
10. Best Practices
    - Use consistent indentation (4 spaces)
    - Leave blank lines between major sections
    - Preview before finalizing to check formatting

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
