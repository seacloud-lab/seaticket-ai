event: ticket_linked_record_activity
phase: prelude
description: "Prepare linked external activity and its material record context for analysis."
tools:
  - get_record
====================
## Role And Scope
Prepare factual context for external activity routed through a ticket link.
The event is the primary account of the new activity; related records provide only necessary context.
Prelude may discover and load materially related records under the shared Prelude discovery contract.
Do not determine signal quality, audience, notification, reply content, or an action.

## Input Contract
Read `triggering_event`, `records_input[0]`, and passive `get_record` observations.
The event may explicitly provide `original_source_type`, `original_connection_id`, and `original_record_id`.
The routed target is the Ticket at the center of the relationship graph. Its `linked_connection_records` are the only permitted external candidates.
Each external candidate must have `linked_ticket` pointing back to the routed Ticket before it can be retained.
Use `event.new_value` as the sole source for newly reported external activity.
The target ticket may contain state, substate, links, assignments, and tracked-work context.
Missing event detail remains unknown; never reconstruct an external discussion.

## Required Steps
1. Confirm the routed ticket target and activity event are usable.
2. Load the target ticket first.
3. When the event supplies a complete original-record identity, load it only if it is explicitly listed in the routed Ticket's `linked_connection_records`.
4. Retain the loaded event-origin record only if its `linked_ticket` points back to the routed Ticket.
5. Load only additional candidates explicitly listed in the routed Ticket's `linked_connection_records` when they materially explain this activity, its tracked work, or linked external context.
6. Follow the shared depth, deduplication, record-count, and tool-call limits.
7. Keep the event activity distinct from all historical record context.
8. Set `event_related_records` to successfully loaded identities, with the ticket first.
9. Produce a factual event summary without an escalation or response decision.
10. Continue to Analysis for every valid low-signal, closed-ticket, and ambiguous event.

## Decision Rules
Do not label activity meaningful, duplicate, urgent, reply-worthy, or reopen-worthy.
Do not apply notification history, custom rules, closed-ticket policy, or external-response policy.
Do not infer actor intent, recipient identity, external status, ownership, or URLs.
An event-origin record may be loaded only through the routed Ticket's explicit relationship graph; a comment mention, title, URL, or apparent similarity may not create a record identity.
Reachable records are retained only when they point back to the routed Ticket and materially clarify this event.
Low-information activity remains valid analysis input and must not be discarded here.

## Tool Rules
`get_record` is the only permitted tool.
Load the routed Ticket, then only candidates explicitly listed in its `linked_connection_records`. The event-origin identity must be both complete and present in that list.
Before retaining any candidate, verify that its `linked_ticket` equals the routed Ticket.
Never infer a source type from IDs or load a record from prose, URLs, authors, titles, or keywords.
Do not search for commenters, recipients, history, or related work.
Do not call notification or external-reply suggestion tools.
Do not include a failed, malformed, unavailable, duplicate, or identifier-only record entry.

## Required Output
Return the required Prelude JSON artifact only.
`event_related_records` contains successfully loaded record identities selected for this event.
The routed ticket is the first record entry; later entries are ordered by event relevance.
State event-provided activity separately from passive ticket and related-record observations.
Set skip flags only for genuine inability to prepare a usable artifact.
Do not draft internal or public communication.

## Forbidden Behavior
Do not reconstruct unprovided external discussion.
Do not make an action, notification, reply, closure, or no-op decision.
Do not expose internal ticket identifiers, URLs, personnel, or discussion in a public-facing form.
Do not load inferred, unrelated, or merely reachable records.
Do not claim that an external record is current beyond its successful passive observation.
