from smolagents import Tool

_TOOL_KETWORDS_SEARCH_RESULT_FORMAT = """The result of this tool has the below format:
Retrieved documents:

===== Document 1 ===== Source: <URL of document 1> =====
<Document 1's content>


===== Document 2 ===== Source: <URL of document 2> =====
<Document 2's content>
"""

Tools = {
    'keywords_search': {
        'type': 'function',
        'function': {
            'name': 'keywords_search',
            'description': f'This is a search tool for technical documents. You can use a query containing the keywords (separated by \',\') related to the issue to find documents related to the issue. The relevance will decrease from the beginning to the end. {_TOOL_KETWORDS_SEARCH_RESULT_FORMAT}. **Note: Not every keyword can be searched for relevant results. Regardless of whether there are results or not, you should not make random searches.**',
            'parameters':{
                'type': 'object',
                'properties': {
                    'query': {
                        'type': 'string',
                        'description': 'The query containing the keywords (separated by \',\') related to the issue.'
                    }
                },
                'required': ['query']
            }
        }
    }
}

class FinalAnswerTool(Tool):
    name = "final_answer"
    description = "Provides a final answer to the given problem."
    inputs = {
        "answer": {
            "type": "any", "description": "The final answer to the problem"
        },
        "sources": {
            "type": "any", "description": "The sources of final answer"
        }
    }
    output_type = "any"

    def forward(self, answer, sources):
        return {
            "answer": answer,
            "sources": sources
        }

