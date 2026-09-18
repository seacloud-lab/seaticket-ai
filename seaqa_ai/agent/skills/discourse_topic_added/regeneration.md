event: discourse_topic_added
phase: regeneration
description: "Regenerate the reply or ticket suggestion for a new Discourse topic from the user's instruction."
tools:
  - suggest_reply
  - suggest_create_ticket
  - suggest_link_existing_ticket
====================
<!-- Derived from handling.md. Keep tools and event-specific constraints in sync. -->
## Role And Scope
- Create the pending suggestion requested by the user for the current unresolved request in a Discourse topic.
- Do not invent the current request, its category, or its resolved state.
- Regeneration turns the requested action into a proposal and nothing more.
- `user_instruction` is the highest-priority input. Analysis is supporting context.

## Input Contract
- Use the triggering event, successful `get_record` tool observations, `analysis_report`, `recommended_actions`, `user_instruction`, `previous_drafts`, and declared Analysis fields.
- `recommended_actions` is the allowed tool range. `skip_handling` is not binding.
- The current unresolved request remains the reply or ticket subject.
- Do not add GitHub issue metadata to a Discourse topic.

## Required Steps
1. Read `user_instruction` first, then Analysis context and any `previous_drafts`.
2. Create the primary suggestion requested by the user:
    - For an evidence-supported reply, call `suggest_reply` with a non-empty reply and reason that address the current unresolved request. Include only selected supporting URLs.
    - For an unresolved report or request, call `suggest_create_ticket` with a non-empty reason that preserves user facts and uncertainty.
    - For an existing-ticket link, call `suggest_link_existing_ticket` with a positive integer `related_ticket` and a non-empty reason; if `related_ticket` is missing or not positive, omit the call and report the constraint.
3. When `previous_drafts` is present, adjust those drafts unless the instruction asks for a full rewrite.
4. Record the actual outcome in the report.

## Decision Rules
- Resolved state does not independently justify no action or a reply.
- Call only one of `suggest_reply`, `suggest_create_ticket`, or `suggest_link_existing_ticket`, never more than one.
- Do not fabricate a URL or present an internal ticket as public evidence.
- Do not turn an uncertain report into a confirmed defect in a ticket.
- Failure does not justify a retry, replacement action, or additional research.
- Suggestion creation is pending work, not publication or resolution.

## Tool Rules
- Only `suggest_reply`, `suggest_create_ticket`, and `suggest_link_existing_ticket` are permitted.
- Do not call `get_record`, search, or undeclared tools.
- Use only an action allowed by this skill and requested by the user instruction.
- Do not use a tool to change topic state, category, tags, or author data.

## Required Output
- State the requested action, call attempted, evidence URL use, and outcome.
- Report failure honestly and avoid claiming a suggestion exists unless the call succeeded.
- After all selected attempts, immediately return the report and make no further tool call.
- Do not add new analysis.

## Forbidden Behavior
- Do not retrieve records or search.
- Do not create suggestions outside the allowed tools.
- Do not disclose internal tickets, secrets, contacts, or unrelated discussion.
- Do not invent facts, URLs, or resolution status.
- Do not report public delivery or closure.

## Examples
- Good: the user asks for a reply that answers the first-post SSO question with its selected documentation URL.
- Good: the user asks for a ticket that preserves the exact reported failure and version uncertainty.
- Bad: propose both actions because the topic is unresolved.
