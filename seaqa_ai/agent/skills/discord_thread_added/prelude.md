event: discord_thread_added
phase: prelude
description: "Prepare a new unlinked Discord thread for analysis."
tools:
  - get_record
====================
## Role And Scope
- Prepare factual context for one newly created Discord thread that has not yet been linked to a Ticket.
- The opening message is the sole record context for this phase.
- A thread's current state is factual context, not a handling decision.

## Input Contract
- Read `triggering_event`, `records_input[0]`, and passive `get_record` observations.
- Thread data may include title, content messages, URL fields, and linked-ticket fields.
- The opening message remains primary even when later messages exist.
- Do not infer tags, issue type, or related identities not explicitly supplied.
- If the successful passive `get_record` observation for the supplied triggering thread is unavailable and its record data is materially needed, use `get_record` only for that same supplied target identity. No prior tool call or error is required.

## Required Steps
1. Confirm the triggering thread and opening message are usable.
2. Use the successful passive target-thread observation and extract its factual request or reported behavior. If it is unavailable and target data is materially needed, actively load only that same supplied target identity.
3. Keep the triggering thread primary. Related context, when used, must be factual and material to this event.
4. Set `event_related_records` to the successfully loaded triggering thread only.
5. Write a neutral factual summary and leave subsequent workflow phases enabled for a usable event.

## Decision Rules
- A setup question is factual context, not yet a classification.
- A failure report is an observation, not a confirmed defect.
- This event represents a newly created, unlinked thread. Do not infer a related identity from markdown links, discussion text, URL fragments, or author hints.

## Context Rules
- Do not treat an inferred identity as factual context.
- A permitted load may use only the supplied triggering thread identity. Do not derive an identity from markdown links, discussion text, URL fragments, author, error details, or apparent similarity, and do not use a load to expand context.

## Required Output
- Return the required Prelude JSON artifact only.
- `event_summary` states the opening-message substance and any material context already present.
- `event_related_records` contains the successfully loaded triggering thread first, followed only by successfully loaded, materially relevant records.
- State when a usable target or event cannot be prepared; Analysis decides the next action.

## Forbidden Behavior
- Do not classify as Bug, Feature, or Question.
- Do not evaluate whether a response is complete or correct.
- Do not expose contacts, credentials, tokens, or unnecessary quotations.
