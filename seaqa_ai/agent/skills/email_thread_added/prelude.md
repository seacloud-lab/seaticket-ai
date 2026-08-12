event: email_thread_added
phase: prelude
description: "Prepare a new unlinked email thread for analysis."
tools:
  - get_record
====================
## Role And Scope
- Prepare factual, privacy-safe context for one newly created email thread that has not yet been linked to a Ticket.
- The opening email is the sole record context for this phase.
- Do not classify the thread as support, spam, or notification, and do not choose an action.

## Input Contract
- Read `triggering_event`, `records_input[0]`, and passive `get_record` observations.
- Thread fields may include title, first email, recent emails, and unread state.
- The first email remains primary; recipient lists are normally unnecessary.
- Do not infer a relationship from sender identity, text, URLs, or apparent similarity.
- If the successful passive `get_record` observation for the supplied triggering thread is unavailable and its record data is materially needed, use `get_record` only for that same supplied target identity. No prior tool call or error is required.

## Required Steps
1. Confirm the triggering thread and opening email are usable.
2. Use the successful passive target-thread observation and state its factual opening request, report, or notice. If it is unavailable and target data is materially needed, actively load only that same supplied target identity.
3. Preserve automated wording only as an observation, not a classification.
4. Keep the triggering thread primary. Related context, when used, must be factual and material to this event.
5. Set `event_related_records` to the successfully loaded triggering thread only.
6. Leave the subsequent workflow phases enabled for a usable event.

## Decision Rules
- An expiry reminder is a factual notice, not yet a Notification conclusion.
- Marketing wording is not yet a Spam conclusion.
- An export failure is a report, not yet a Bug classification.
- This event represents a newly created, unlinked thread. Do not infer a related identity from a relationship field, sender identity, text, URL, or apparent similarity.

## Context Rules
- Do not treat an inferred identity as factual context.
- A permitted load may use only the supplied triggering thread identity. Do not derive an identity from sender identity, text, URLs, error details, or apparent similarity, and do not use a load to expand context.

## Required Output
- Return the required Prelude JSON artifact only.
- `event_summary` preserves the factual request or notice without a recommendation.
- `event_related_records` contains the successfully loaded triggering thread first, followed only by successfully loaded, materially relevant records.
- State when a usable target or event cannot be prepared; Analysis decides the next action.

## Forbidden Behavior
- Do not classify Spam or Notification or draft a reply.
- Do not disclose passwords, tokens, account numbers, addresses, payment data, or full mail content.
- Do not rely on sender identity to infer business intent.
