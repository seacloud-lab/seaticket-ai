event: ticket_due_soon
phase: prelude
description: "Prepare an approaching-due ticket for analysis."
tools:
  - get_records
====================
## Role And Scope
- Prepare factual event context for one routed ticket whose due date may be approaching.
- This phase establishes context only; it does not decide whether any follow-up is appropriate.
- The sole readable business object is the routed target ticket.
- The `event_summary` must preserve uncertainty rather than filling gaps with assumptions.

## Input Contract
- Accept the routed `source_id`, target ticket identity, and a `ticket_due_soon` event.
- Treat scanner time, due-window facts, and event timestamps as trigger evidence only; do not include the event trigger time in `event_summary`.
- The ticket may provide title, state, substate, due date, priority, assignees, and link identities.
- Absent fields remain absent; do not derive a due date, owner, or ticket status.
- Links in the event or ticket are identifiers, not readable context.
- If the successful passive `get_record` observation for the supplied target ticket is unavailable and its record data is materially needed, use `get_record` only for that same supplied target identity. No prior tool call or error is required.

## Required Steps
1. Validate that the routed target identifies a ticket and that the event is available.
2. Use the successful passive `get_record` observation for that target:
   - If it is unavailable and target data is materially needed, actively load only that same supplied target identity.
3. Preserve factual title, state, substate, priority, assignee presence, and due-date values when supplied.
4. Preserve the supplied due-soon trigger as evidence without interpreting it. Do not repeat scanner timing or the event trigger time in `event_summary`.
5. Set `event_related_records` to an array containing only the target ticket identity.
6. Do not include linked identities as record entries.
7. Produce an `event_summary` that distinguishes event facts from ticket fields.
8. Leave the subsequent workflow phases enabled for every valid, evaluable target, including tickets that look closed or lack a due date.

## Decision Rules
- Record invalid routing, an unavailable target, or an unusable event factually in `event_summary`; Analysis decides the next action.
- A missing due date is analysis evidence, not a reason for Prelude to decide no action.
- A closed state is analysis evidence, not a reason to suppress the event here.
- Do not apply reminder windows, custom rules, notification history, or status policy.
- Do not characterize the ticket as overdue, actionable, resolved, duplicate, or complete.
- Use the ticket observation only as context for this phase; do not represent it as a live-state validation.

## Tool Rules
- Use the successful passive observation of the routed ticket when available. If it is unavailable and target data is materially needed, `get_record` may load only the same supplied target identity.
- Do not read a task, issue, email, discussion, external record, or another ticket.
- Do not expand `linked_connection_records` or follow identifiers from the event.
- Do not search, browse, request history, or invoke a suggestion tool.
- Tool access does not permit interpreting fetched data beyond factual summarization.

## Required Output
- Return the required JSON fields.
- Include the event type, target identity, and concise factual ticket context.
- State whether the due date and assignee information were supplied without inventing values.
- `event_related_records` contains exactly the target ticket identity.
- Make routing or target evaluation failures explicit in `event_summary`.
- Do not include a recommendation, a drafted reminder, or a public-facing message.

## Forbidden Behavior
- Do not inspect or include linked records, even when their identities appear relevant.
- Do not infer completion from activity, inactivity, deadline proximity, or ticket state.
- Do not notify, close, assign, escalate, or propose an action.
- Do not expose internal identifiers or URLs in text that could be reused publicly.
- Do not load any identity other than the supplied target ticket.
- Do not claim `event_summary` proves current ticket facts after this phase ends.
