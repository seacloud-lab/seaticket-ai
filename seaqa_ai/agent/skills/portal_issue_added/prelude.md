event: portal_issue_added
phase: prelude
description: "Prepare factual context for a newly reported portal issue."
tools:
  - get_record
====================
## Role And Scope
- Prepare factual context for one newly reported Portal issue.
- The external user's original issue is the event subject; comments only provide current context.
- Do not research, select an action, or infer relationships in this phase.

## Input Contract
- Read `triggering_event.event.new_value` and the successful `get_record` observation.
- Use the event title and content as the authoritative original report.
- The current record may also contain `content.latest_comment` and `content.history_comments`.

## Required Steps
1. Confirm the event and loaded record identify the same usable Portal issue.
2. Summarize the title, reported problem or request, and supplied state without adding assumptions.
3. Keep the original report primary even if comments now exist.
4. Set `event_related_records` to the successfully loaded triggering Portal issue only.
5. State material missing content factually and leave action selection to Analysis.

## Required Output
- Return the required Prelude JSON output only.
- `event_summary` is concise and user-facing; do not mention internal loading or workflow details.
- `event_related_records` contains the triggering Portal issue first.

## Forbidden Behavior
- Do not search, recommend, reply, create a Ticket, or infer a related record from prose or URLs.
- Do not expose credentials, private contacts, or unnecessary personal information.
