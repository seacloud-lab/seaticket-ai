event: email_thread_added
phase: prelude
description: "Prepare a new email thread for analysis."
tools:
  - get_record
====================
## Role And Scope
- Prepare factual, privacy-safe context for one newly created email thread that has not yet been linked to a Ticket.
- The opening email is the sole record context for this phase.
- Do not choose an action.

## Input Contract
- Read `triggering_event` and `get_record` observations.
- The event opening email is `triggering_event.event.new_value`: its `message_id`, `email_date`, `is_sender`, `email_from`, and `content`. The thread title is `triggering_event.event.new_value.title`.
- The current latest email is `get_record.content.latest_email`. Earlier emails, oldest first and excluding the latest, are in `get_record.content.history_emails`.
- The opening email remains primary; recipient lists are normally unnecessary.
- Do not infer a relationship from sender identity, text, URLs, or apparent similarity.

## Required Steps
1. Confirm the triggering thread and opening email are usable.
2. Use the event opening email only when it has a usable `message_id` and `content`. Treat the event opening email as stale only when `triggering_event.event.new_value.message_id` and `get_record.content.latest_email.message_id` are both present and differ. When stale, state that the thread has a later email and summarize both the opening email and the current latest email. When both message IDs match, summarize the opening email as the current request.
3. If the event payload does not identify a usable opening email, or either message ID is unavailable, state in `event_summary` that whether the opening email is still the latest in the thread cannot be verified. Use the earliest available email (the first of `get_record.content.history_emails`, or `get_record.content.latest_email` when that list is empty) and the current latest email only as factual context; do not call the opening email stale.
4. Preserve automated wording as factual context.
5. Keep the triggering thread primary. Related context, when used, must be factual and material to this event.
6. Set `event_related_records` to the successfully loaded triggering thread only.
7. Leave the subsequent workflow phases enabled for a usable event.

## Decision Rules
- This event represents a newly created, unlinked thread. Do not infer a related identity from a relationship field, sender identity, text, URL, or apparent similarity.
- Use `is_sender` to describe email direction: `false` is the other party and `true` is our side. Do not infer direction from addresses.

## Context Rules
- Do not treat an inferred identity as factual context.
- Do not make additional `get_record` calls or derive an identity from sender identity, text, URLs, error details, or apparent similarity.

## Required Output
- **Return the required Prelude JSON output only.**
- `event_summary` preserves the factual request or notice without a recommendation.
- `event_summary` is user-facing. Do not mention `unlinked`, Ticket association status, record loading or linking, or other internal workflow terms. For example, you can start with `A new email thread ...`.
- `event_related_records` contains the successfully loaded triggering thread first, followed only by successfully loaded, materially relevant records.
- State when a usable target or event cannot be prepared; Analysis decides the next action.
- State missing opening-email data factually in `event_summary`.

## Forbidden Behavior
- Do not draft a reply.
- Do not disclose passwords, tokens, account numbers, addresses, payment data, or full mail content.
- Do not rely on sender identity to infer business intent.
