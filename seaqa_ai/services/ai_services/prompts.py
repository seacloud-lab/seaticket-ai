AI_UTILS_PROMPTS_MAP = {
    'generate_ticket': "You are an expert ticket-processing assistant. Your task is to create well-structured tickets from provided information. Always follow the explicit output language requirement provided in the prompt.",
    'generate_kb_record_from_ticket': "You are an expert knowledge base editor. Your task is to convert a ticket into a reusable knowledge base record. Always follow the explicit output language requirement provided in the prompt.",
    'rank_relevant_records': "You are an expert on relevance ranking. Your task is to analyze records and identify only those that are truly relevant to the query"
}

class UserContentPrompts:
    @staticmethod
    def generate_ticket(record_detail, target_lang='English'):
        return f"""Based on the provided ticket information, please generate a concise and informative ticket.

{record_detail}

**Instructions:**
1. Generate a new, clear, and concise title for the ticket.
2. Create a detailed description that summarizes the key points from the body content.
3. The output language must be {target_lang}.
4. Return the result in JSON format with "title" and "description" keys.

**Example Output:**
{{
    "title": "New Ticket Title",
    "description": "A detailed summary of the ticket body."
}}"""

    @staticmethod
    def generate_kb_record_from_ticket(ticket_title, ticket_content, ticket_comments=None, target_lang='English'):
        comments_lines = []
        for c in (ticket_comments or [])[:50]:
            if not isinstance(c, dict):
                continue
            content = (c.get('content') or '').strip()
            if not content:
                continue
            author = (c.get('author') or c.get('creator') or c.get('nickname') or '').strip()
            created_time = (c.get('created_time') or c.get('commented_at') or '').strip()
            prefix = '- '
            if author or created_time:
                prefix = f"- ({created_time}) {author}: "
            comments_lines.append(prefix + content)
        comments_text = '\n'.join(comments_lines) if comments_lines else '(no comments)'

        return f"""Convert the following ticket into a knowledge base record.
Ticket Title:
{ticket_title}

Ticket Content:
{ticket_content}

Ticket Comments:
{comments_text}

**Requirements:**
1. The output language must be {target_lang}.
2. kb_title should be concise and suitable as a KB record title.
3. kb_content should be a clean, structured article in plain text/markdown.
4. If comments provide important clarifications, root cause, workaround, or resolution, incorporate them.
5. Do NOT include any additional text, explanations, or markdown fences outside the JSON.

**Output JSON format (MUST follow exactly):**
{{
  "kb_title": "...",
  "kb_content": "..."
}}
"""

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

GENERATE_SUMMARY_PROMPTS_MAP = {
    'github_issue': "You are a GitHub issue analysis expert. Read the issue's title and body, then craft a single-sentence summary describing the core problem. Keep it concise (no more than 100 characters) and return only that sentence without extra commentary or formatting.",
    'discourse_forum': "You are a Discourse forum post analysis expert. Read the post's title and body, then craft a single-sentence summary describing the core topic or request. Keep it concise (no more than 100 characters) and return only that sentence without extra commentary or formatting.",
    'email': "You are a email analysis expert. Read the email's title and body, then craft a single-sentence summary describing the core topic or request. Keep it concise (no more than 100 characters) and return only that sentence without extra commentary or formatting.",
    'ticket': "You are a ticket analysis expert. Read the ticket's title and body, then craft a single-sentence summary describing the core topic or request. Keep it concise (no more than 100 characters) and return only that sentence without extra commentary or formatting.",
    'documents': "You are an expert in summarizing the documents. A document consists of a title and content. Since the document may contain technical details, your summary should clearly and effectively capture the main points and key idea of the document (especially highlight the keywords to facilitate content search), rather than the technical details themselves (i.e., you need to briefly say what this record do or suggest, e.g., 'This document provide a suggetion how to fix synced libraries failure with 447 error since it has too many files by modifying client settings'). Please keep it concise (no more than 200 characters and do not include any Markdown-format text) and return only that one sentence without extra commentary or formatting.",
    'notion': "You are a Notion page analysis expert. Read the page's title and content, then craft a single-sentence summary describing the core topic or request. Keep it concise (no more than 100 characters) and return only that sentence without extra commentary or formatting.",
    'general_task': "You are a task management expert. Read the task's title and description, then craft a single-sentence summary capturing the primary objective or required action. Keep it concise (no more than 100 characters) and return only that sentence without extra commentary or formatting."
}
