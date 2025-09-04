from smolagents import Tool


class RetrieverTool(Tool):
    name = "retriever"
    description = "Uses semantic search to retrieve the parts of transformers documentation that could be most relevant to answer your query."
    inputs = {
        "query": {
            "type": "string",
            "description": "The query to perform. This should be semantically close to your target documents. Use the affirmative form rather than a question.",
        }
    }
    output_type = "string"

    def __init__(self, retriever, project_uuid, connection_types=None, connection_ids=None, **kwargs):
        super().__init__(**kwargs)
        self.project_uuid = project_uuid
        self.connection_types = connection_types
        self.connection_ids = connection_ids

        self.retriever = retriever

    def forward(self, query: str) -> str:
        """Execute the retrieval based on the provided query."""
        assert isinstance(query, str), "Your search query must be a string"

        # Retrieve relevant documents
        docs = self.retriever.search(query, self.project_uuid, self.connection_types, self.connection_ids)

        # Format the retrieved documents for readability
        return "\nRetrieved documents:\n" + "".join(
            [
                f"\n\n===== Document {str(i)} =====\n" + doc.get('content')
                for i, doc in enumerate(docs)
            ]
        )

