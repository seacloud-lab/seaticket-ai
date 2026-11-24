event: email_message_received
phase: prelude
description: "Prepare a new email message and material related context for analysis."
tools:
  - get_records
====================
## Role And Scope
- Prepare factual context for one newly added email message.
- **The new message is the event subject; the thread and related records are background only.**
- Do not research or recommend a reply, ticket, or mailbox action.

## Input Contract
- Each `get_records` observation is an array. Select the successful item whose `source_type`, `connection_id`, and `record_id` match the triggering record's identity supplied in the framework's `get_records` targets; call it `current_record`. It must contain `content` and have neither `error` nor `notice`. Do not select by array position or use a related record as the triggering record.
- A duplicate notice supplies no content; use an already supplied successful item for the same identity. If none is available, the current content is unavailable: do not infer that the event is current or stale, and apply the missing-ID rules below.
- Read `triggering_event.event.new_value` and `get_records` observations.
- The event email is `triggering_event.event.new_value`. The current latest email is `current_record.content.latest_email`.
- **Use only record context available in successful `get_records` observations.**
- Recipient information is normally unnecessary and sensitive content must remain minimized.
- Missing message content or thread identity is a material data gap.

## Required Steps
1. Confirm the parent thread and new message are usable.
2. Treat the event email as stale only when `triggering_event.event.new_value.message_id` and `current_record.content.latest_email.message_id` are both present and differ. State that the thread has a later email and summarize both the event email and the current latest email.
3. If either message ID is unavailable, state that whether the event email is still current cannot be verified. Summarize the available event email and current latest email factually without calling the event email stale.
4. Keep the event email primary as the original trigger. When it is stale, do not present it as the current latest request.
5. Set `event_related_records` to successfully loaded, materially relevant identities, with the thread first.
6. State missing message or target identity factually in `event_summary`.

## Decision Rules
- Sender identity, automation, recipients, thanks, or quoted history do not determine action.
- `is_sender: false` identifies an incoming message from the other party; `is_sender: true` identifies an outgoing message from our side. Use the same field on `latest_email` to describe the direction of later progress.
- Do not infer whether the event email is current from content, author, or timestamps.
- Mail text, URLs, names, and apparent similarity cannot identify a related record.

## Context Rules
- **Do not infer or reverse-validate relationships, or make additional `get_records` calls.**
- Do not derive an identity from mail text, URLs, names, error details, or apparent similarity.

## Required Output
- **Return the required Prelude JSON output only.**
- `event_summary` focuses on the newly added message without a recommendation.
- `event_related_records` contains successfully loaded identities, with the thread first.
- Do not include action or skip fields.

## Forbidden Behavior
- Do not decide a mailbox action, `no_op`, reply, or ticket creation.
- Do not load inferred, unrelated, or merely reachable records.
- Do not expose credentials, personal addresses, payment details, tokens, or irrelevant quotations.
- Do not reconstruct the entire email thread.
