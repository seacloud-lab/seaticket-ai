from smolagents import Tool

_TOOL_KETWORDS_SEARCH_RESULT_FORMAT = """The result of this tool has the below format (ranked by keyword relevance):
Retrieved documents:

===== Document 0 ===== Title: <title of document 0> =====
<Document 0's content>

Retrieved documents:


===== Document 1 ===== Title: <title of document 1> =====
<Document 0's content>
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
            "type": "string", 
            "description": "The final answer to the problem"
        },
        "document_indexes": {
            "type": "array", 
            "items": {
                "type": "integer"
            },
            "description": "The document_indexes of final answer, should compose of indexes (int) of the search tool results."
        }
    }
    output_type = "any"

    def forward(self, answer, document_indexes):
        return {
            "answer": answer,
            "document_indexes": document_indexes
        }

