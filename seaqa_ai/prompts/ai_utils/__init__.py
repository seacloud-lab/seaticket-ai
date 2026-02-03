SYSTEM_PROMPTS_MAP = {
    'generate_ticket': "You are an expert ticket-processing assistant. Your task is to create well-structured tickets from provided information.",
    'rank_relevant_records': "You are an expert on relevance ranking. Your task is to analyze records and identify only those that are truly relevant to the query"
}

class UserContentPrompts:
    @staticmethod
    def generate_ticket(record_detail):
        return f"""Based on the provided ticket information, please generate a concise and informative ticket.

{record_detail}

**Instructions:**
1. Generate a new, clear, and concise title for the ticket.
2. Create a detailed description that summarizes the key points from the body content.
3. The output language must be consistent with the input language.
4. Return the result in JSON format with "title" and "description" keys.

**Example Output:**
{{
    "title": "New Ticket Title",
    "description": "A detailed summary of the ticket body."
}}"""
    
    @staticmethod
    def rank_relevant_records(query_record, candidates_text):
        return f"""You are an expert in analyzing the relevance between records. Given a query record and a list of candidate records, your task is to identify and rank ONLY the relevant candidates.

Query Record:
{query_record}

Candidate Records:
{candidates_text}

1. Analyze the semantic similarity and topical relevance between the query record and each candidate record.
2. Consider both the title and other fields when determining relevance. Meanwhile, the title field of the candidate record has a higher relevance weight.
3. ONLY include records that are truly relevant to the query record. A record is relevant if it:
    - Addresses similar title, topics, issues, or problems
    - Has related technical content or features
    - Shares common themes or concepts
    - Could provide useful context or related information
4. Exclude records that are unrelated, off-topic, or have no meaningful connection to the query.

**OUTPUT FORMAT REQUIREMENTS:**
- You MUST output a valid JSON object with EXACTLY this structure:
{{
  "results": [
    {{"connection_id": "string_value", "record_id": "string_value"}},
    {{"connection_id": "string_value", "record_id": "string_value"}}
  ]
}}
- The "results" array must contain objects ordered from most relevant to least relevant.
- If no records are relevant, set "results" to an empty array: []
- Each "connection_id" and "record_id" MUST be string values (convert numbers to strings if needed).
- Do NOT include any additional text, explanations, or markdown formatting outside the JSON.
"""
