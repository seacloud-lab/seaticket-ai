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
    description = "Provides a final answer to the given problem. `answer` MUST include [Reference n] markers where n starts at 1 and corresponds to `document_indexes` order. **Each** index in `document_indexes` MUST appear **at least once** as a [Reference n] in `answer`, otherwise it should not exist in `document_indexes`."
    inputs = {
        "answer": {
            "type": "string", 
            "description": "The final answer to the problem. This argument will be written in Markdown format, and should be as aesthetically pleasing and easy for readers to understand as possible. Lists should be displayed across multiple lines, and tables should be used flexibly. **Each** element in argument `document_indexes` MUST appear **at least once** as a [Reference n] in this argument. Never invent references - only use [Reference n] when corresponding `document_indexes` exist."
        },
        "document_indexes": {
            "type": "array", 
            "items": {
                "type": "integer"
            },
            "description": "The `document_indexes` of final answer, should compose of indexes (int) of the search tool results. If this argument is not empty (e.g., [0,2]), `answer` MUST include [Reference n] markers where n starts at 1 and corresponds to `document_indexes` order. **Each** index in `document_indexes` MUST appear **at least once** as a [Reference n] in `answer`, otherwise it should not exist in this argument."
        }
    }
    output_type = "any"

    def forward(self, answer, document_indexes):
        return {
            "answer": answer,
            "document_indexes": document_indexes
        }

