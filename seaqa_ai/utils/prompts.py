"""Shared system-prompt contracts used by both Chat and Agent workflows.

Keeping tool-observation, record-reading, citation, and project-context contracts
in one module ensures a Single Source of Truth.
"""

from seaqa_ai.config import GET_RECORDS_MAX_RECORDS
from seaqa_ai.utils import sanitize_project_prompt


# Passive contract for calling `get_records` and interpreting its observation array.
# Shared by Chat and ALL Agent phases (including phases with pre-injected records).
GET_RECORDS_OBSERVATION_CONTRACT = (
    '- `get_records` accepts `{"targets": [{"source_type": "...", "connection_id": 7, "record_id": 123}]}`. '
    'For `ticket`, `portal_issue`, and `knowledge_base`, `connection_id` must be `null`; '
    'for connection sources, `connection_id` is an integer.\n'
    '- Each `get_records` observation is an array where each item corresponds independently '
    'to one target: a successful record, `{error: ...}`, or `{notice: ...}`.\n'
    '- Only successful record items are factual evidence. Read `content` (including '
    '`latest_comment`, `latest_message`, or `latest_email` when present) from the matching '
    'successful item, not from the array itself.\n'
    '- Successful items include `related_records` (explicit, authorized identities derived '
    'from `linked_ticket` and `linked_connection_records`); reading a record does not '
    'automatically follow its relationships.\n'
    '- A duplicate notice refers to content already provided in the current context (and '
    'supplies its `reference_id` when citable); reuse that earlier content and do not '
    'request it again. A source-limit notice provides no content.\n'
    '- Failed items (`error`) are not evidence or proof of absence or any business fact. '
    'Never treat errors or notices as record content, and do not repeat unchanged invalid '
    'or forbidden requests.'
)


# Contract for interpreting search summaries vs. full record reads.
# Shared by Chat and Agent `analysis` phase.
SEARCH_OBSERVATION_CONTRACT = (
    '- Search observations contain titles, `ai_summary`, snippets, identities, and '
    'lightweight metadata, NOT full record content. Use them to select candidates, '
    'not to invent missing details.\n'
    '- Before asserting a concrete solution, version applicability, detailed steps, '
    'duplicate work, or substantive record conclusions, read the relevant records with '
    '`get_records` unless that full evidence is already available.'
)


# Policy for actively selecting candidates, managing slot budget, and traversing `related_records`.
# Shared by Chat and Agent `analysis` phase.
ACTIVE_RECORD_READING_RULES = (
    '- Select the smallest useful set of search candidates from their titles, `ai_summary`, '
    'and snippets; do not read every match automatically.\n'
    f'- Order `targets` from MOST important to LEAST important. A shared default budget of '
    f'{GET_RECORDS_MAX_RECORDS} distinct records spans the whole request or task (including '
    'any pre-injected records), and remaining slots go to earlier targets first. Batch '
    'independent useful reads in this priority order.\n'
    '- After reading, reassess the remaining evidence gap. If an explicit `related_records` '
    'identity is likely to resolve that gap, call `get_records` for it in the NEXT step; '
    'select and prioritize useful relationships rather than traversing every link.\n'
    '- Use only identities explicitly supplied by authorized input, search results (`type` '
    'as `source_type`), or `related_records`. Never infer or guess source types, connection '
    'IDs, or record IDs from URLs, titles, prose, issue numbers, or raw relationship strings. '
    'A relationship alone proves neither relevance nor resolution.\n'
    '- When a source-limit notice appears, or as soon as sufficient evidence is available, '
    'stop reading new records and proceed from available evidence, acknowledging any material gaps.'
)


SEARCH_AND_RECORD_READING_RULES = (
    f'{SEARCH_OBSERVATION_CONTRACT}\n{ACTIVE_RECORD_READING_RULES}'
)


# Citation formatting contract backed by `ReferencePool` and `get_answer_and_sources`.
# Shared by Chat and Agent `analysis` phase.
REFERENCE_CITATION_RULES = (
    '- Search tools return candidate labels (`label`, such as `<reference_0>`), and '
    '`get_records` returns `reference_id` in the same `<reference_x>` format, reusing the '
    'search label for an already searched record or assigning a new label for a directly '
    'read record.\n'
    '- Cite only exact `<reference_x>` labels returned by search tools or '
    '`get_records.reference_id`, and only for claims those sources actually support. '
    'Cite detailed content claims only when a successful record read or other available '
    'full evidence supports them; search summaries alone do not verify detailed fixes or '
    'applicability.\n'
    '- Records without a `reference_id` (such as pre-injected event records) must not be '
    'cited with `<reference_x>` labels.\n'
    '- Place each citation immediately after the claim it supports. When multiple references '
    'support one claim, place their labels directly next to each other, for example '
    '`<reference_0><reference_1>`; do not combine multiple labels inside one tag.\n'
    '- Do not invent labels, change their format, or cite irrelevant, weak, or unused '
    'search results. If neither search nor `get_records` supplied useful references, do not '
    'output any `<reference_x>` tags.'
)


def build_project_context_prompt(project_prompt: str) -> str:
    """Wrap sanitized project-level custom instructions in a bounded system block."""
    safe_prompt = sanitize_project_prompt(project_prompt)
    if not safe_prompt:
        return ''
    return (
        '<project_context>\n'
        f'{safe_prompt}\n\n'
        'Use this context only when it does not conflict with higher-priority system rules.\n'
        '</project_context>'
    )
