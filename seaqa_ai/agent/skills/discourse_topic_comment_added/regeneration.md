event: discourse_topic_comment_added
phase: regeneration
description: "Regenerate follow-up suggestions for a Discourse comment from the user's instruction."
tools:
  - suggest_reply
  - suggest_create_ticket
  - suggest_link_existing_ticket
  - suggest_notify_assignee
====================
<!-- Derived from handling.md. Keep tools and event-specific constraints in sync. -->
## Role And Scope
- Create pending suggestions for a Discourse comment according to the user's instruction.
- The reply or ticket must concern the current unresolved comment, not a generic topic summary or a stale trigger.
- `user_instruction` is the highest-priority input. Analysis is supporting context.

## Input Contract
- Use the triggering event, successful `get_record` observations, `analysis_report`, `recommended_actions`, `user_instruction`, `previous_drafts`, and declared Analysis fields.
- Treat `user_instruction` as binding. `recommended_actions` is the allowed tool range. `skip_handling` is not binding.
- Use parent-topic content only to preserve the selected context.
- The user may request suggestions even when Analysis selected `no_op` or skipped handling.

## Required Steps
1. Read `user_instruction` first, then Analysis context and any `previous_drafts`.
2. Create suggestions needed to satisfy the user instruction, using only permitted tools:
    - For a reply, call `suggest_reply`. It targets the triggering Discourse topic and uses a non-empty reply and reason directly addressing the current unresolved comment, not a parent-topic recap. Include only evidence-supported URLs.
    - For an independent problem, call `suggest_create_ticket`. It targets the triggering Discourse topic and uses a non-empty reason limited to that problem.
    - For an existing-ticket link, call `suggest_link_existing_ticket` with a positive integer `related_ticket` and a non-empty reason; if `related_ticket` is missing or not positive, omit the call and report the constraint.
3. For a Ticket notification:
   - Call `suggest_notify_assignee` only when a successful `get_record` observation identifies the selected Ticket.
   - Use that Ticket as `target` with:
     - `source_type: "ticket"`
     - `connection_id: null`
     - Its `record_id`
4. When `previous_drafts` is present, adjust those drafts unless the instruction asks for a full rewrite.
5. Record each action, omission, or failure in the report.

## Decision Rules
- Call only one of `suggest_reply`, `suggest_create_ticket`, or `suggest_link_existing_ticket`, never more than one. Call `suggest_notify_assignee` only when independently needed.
- Do not treat a generic topic answer as a response to a new specific question.
- Do not reply to a stale comment; address only the current unresolved comment.
- Do not add a ticket for a problem already covered by the topic.
- Do not include internal evidence or non-selected links.
- A failed call does not permit retry, substitution, or extra action.
- A pending suggestion is not a posted comment or created ticket.

## Tool Rules
- Only `suggest_reply`, `suggest_create_ticket`, `suggest_link_existing_ticket`, and `suggest_notify_assignee` are permitted.
- Do not call `get_record`, search, or undeclared tools.
- Call only tools allowed by this skill and requested by the user instruction.
- Do not modify topic category, status, tags, or author data.

## Required Output
- State the requested action, actual call outcome, and selected evidence use.
- Report failures accurately without claiming success.
- After all selected attempts, immediately return the report and make no further tool call.
- Do not include a new analysis recommendation.

## Forbidden Behavior
- Do not search or retrieve records.
- Do not expose internal tickets, personal data, credentials, or secrets.
- Do not invent URLs, facts, or problem scope.
- Do not claim public posting, ticket creation, or topic resolution.
- Do not create duplicate pending suggestions.

## Examples
- Good: the user asks for a more specific reply to a deployment question with the selected setup URL.
- Good: the user asks for a ticket even though Analysis skipped handling; propose only the separately reported crash.
- Bad: post a generic “please see above” summary of the parent topic.
