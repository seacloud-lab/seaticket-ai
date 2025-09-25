from smolagents import Tool


class KeywordsSearch(Tool):
    name = "keywords_search"
    description = "This is a search tool for technical documents. You can use a query containing the keywords (separated by ',') related to the issue to find documents related to the issue. The relevance will decrease from the beginning to the end."
    inputs = {
        "query": {
            "type": "string",
            "description": "The query containing the keywords (separated by ',') related to the issue.",
        }
    }
    output_type = "string"

    def __init__(self, retriever, project_uuid, connection_ids=None, **kwargs):
        super().__init__(**kwargs)
        self.project_uuid = project_uuid
        self.connection_ids = connection_ids

        self.retriever = retriever

    def forward(self, query: str) -> str:
        """Execute the retrieval based on the provided query."""
        assert isinstance(query, str), "Your search query must be a string"

        # Retrieve relevant documents
        docs = self.retriever.search(query, self.project_uuid, self.connection_ids)

        # Format the retrieved documents for readability
        return "\nRetrieved documents:\n" + "".join(
            [
                f"\n\n===== Document {str(i + 1)} ===== Source: {doc.get('url')} =====\n" + doc.get('content')
                for i, doc in enumerate(docs)
            ]
        )


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

class ResolveTicketTool(Tool):
    name = 'resolve_ticket'
    description = "Analyzes the ticket contents and determines how to resolve the ticket. Returns a search query for finding relevant documentation."
    inputs = {
        "ticket_title": {
            "type": "string", 
            "description": "The title of the ticket that needs to be resolved"
        },
        "ticket_description": {
            "type": "string",
            "description": "The content of the ticket that needs to be resolved"
        }
    }
    output_type = "string"

    def forward(self, ticket_title, ticket_description):
        """Analyze the ticket and generate a search query for relevant documentation."""
        if ticket_title and ticket_description:
            search_query = f"{ticket_title}, {ticket_description}"
        elif ticket_title:
            search_query = ticket_title
        else:
            search_query = ticket_description

        print(ticket_title, ticket_description)
        print(search_query)
        return f"Search query generated for ticket resolution: {search_query}"
