event: email_message_added
phase: prelude
description: "Prepare a new email message and material related context for analysis."
tools:
  - get_record
====================
## Role And Scope
Prepare factual context for one newly added email message.
The new message is the event subject; the thread and related records are background only.
Do not classify, research, or recommend a reply, ticket, or mailbox action.

## Input Contract
Read `triggering_event.event.new_value`, `records_input[0]`, and passive `get_record` observations.
The parent thread may expose one `linked_ticket`; a loaded Ticket may expose `linked_connection_records`.
Candidates must be explicitly listed in that Ticket's `linked_connection_records`.
Recipient information is normally unnecessary and sensitive content must remain minimized.
Missing message content or thread identity is a material data gap.

## Required Steps
1. Confirm the parent thread and new message are usable.
2. Load the parent thread first and summarize what the new message asks, reports, confirms, supplies, or acknowledges.
3. Load the parent thread's `linked_ticket` only when Ticket context is materially needed. Do not load it merely because the field exists.
4. If a Ticket was loaded and more context is materially needed, inspect only its `linked_connection_records` and load explicitly listed candidates.
5. Keep `event.new_value` primary; never replace it with an older mail or related context.
6. Follow the shared Prelude discovery limits and stop once sufficient context exists.
7. Set `event_related_records` to successfully loaded identities, with the thread first.
8. State missing message or target identity factually in `event_summary`.

## Decision Rules
An email asking a question or reporting continued failure is factual context, not a classification.
Sender identity, automation, recipients, thanks, or quoted history do not determine action.
Use only the Ticket-centered relationship graph: parent thread -> `linked_ticket` -> Ticket -> `linked_connection_records` -> records that point back to that Ticket. Mail text, URLs, names, and apparent similarity cannot identify a related record.

## Tool Rules
`get_record` is the only permitted tool.
Load the parent thread's `linked_ticket` only when Ticket context is materially needed. Once loaded, load only candidates explicitly listed in that Ticket's `linked_connection_records` under the shared Prelude discovery contract.
Do not infer or reverse-validate relationships. The framework retains only selected, successfully loaded candidates that are explicitly listed by that Ticket.
Do not fetch contacts, attachments, historical messages, or records inferred from mail text.
Never search or create suggestions.

## Required Output
Return the required Prelude JSON artifact only.
`event_summary` focuses on the newly added message without a recommendation.
`event_related_records` contains successfully loaded identities, with the thread first.
Do not include classification, action, or skip fields.

## Forbidden Behavior
Do not decide spam, notification, no-op, reply, or ticket creation.
Do not load inferred, unrelated, or merely reachable records.
Do not expose credentials, personal addresses, payment details, tokens, or irrelevant quotations.
Do not reconstruct the entire email thread.
