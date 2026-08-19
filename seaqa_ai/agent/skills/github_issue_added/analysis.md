event: github_issue_added
phase: analysis
description: "Classify and research a new GitHub issue."
tools:
  - issues_search
  - documents_search
additional_output:
  original_issue_type:
    type: string
    description: >
      The exact issue type supplied by the source record. Preserve it without
      normalization, inference, or reclassification.
  classified_issue_type:
    type: string
    enum: [Bug, Feature, Question]
    description: >
      The issue type classified by Analysis from the issue title and body.
      Use one of Bug, Feature, or Question.
====================
## Role And Scope
- Classify and research one newly created GitHub issue.
- Produce an auditable recommendation for the subsequent Handling phase, not a live change.
- Classify only as `Bug`, `Feature`, or `Question`.
- The issue title and body are primary; existing type and labels are evidence of current state, not authority.

## Input Contract
- Use only records returned by successful passive observations, `triggering_event`, `event_summary`, and search observations made in this phase.
- Treat the successful passive issue observation as immutable input.
- Use `event_summary` and successful `get_record` tool observations for target identity and factual context, not as a substitute for source facts.
- Use supplied `issue_type`, `labels`, and `available_labels` without inventing values.
- Do not refresh records, inspect external records directly, or invoke suggestions.
- Never use information from a search result unless its returned label is available for citation.

## Required Steps
1. Confirm `event_summary`, `triggering_event`, and successful `get_record` tool observations identify a usable issue and opening content; otherwise recommend `no_op` and set `skip_handling` to `true`.
2. Read title and body and identify the primary requested outcome.
3. Record the supplied `issue_type` exactly as `original_issue_type`, then set `classified_issue_type` from the title and body:
   - Use `Bug` for broken, failed, incorrect, or unexpected behavior.
   - Use `Feature` for a requested unavailable capability.
   - Use `Question` for explanation, configuration, documentation, or how-to requests.
   - For mixed content, prefer Bug, then Feature, then Question.
4. Choose the first search tool from the classification and issue a focused query only when evidence can change the action.
5. Evaluate each result for direct relevance and retain only decisive evidence.
6. Use related identities with successful passive observations to avoid creating a duplicate ticket when they already track the same concrete work.
7. Return one primary action plus any independently justified type or label action. Include the issue-type fields because this skill supports Analysis-selected type-change suggestions.

## Decision Rules
- For the primary action:
  - Recommend `suggest_reply` only for a direct supported answer or a clearly tracked case with a usable URL.
  - A known case requires strong overlap in symptom, requested capability, or root problem.
  - General topical similarity, a shared product area, or a matching keyword is insufficient.
  - A related Ticket prevents `suggest_create_ticket` only when its successfully observed content clearly tracks the same concrete work.
  - A related Ticket is context, not search evidence or a direct answer, unless it provides a usable public URL and directly answers the issue.
  - Otherwise recommend `suggest_create_ticket` for an unresolved issue.
- For issue metadata:
  - Add `suggest_modify_type` only with high confidence when the classified type differs substantively from the supplied type or the supplied type is empty.
  - Add `suggest_assign_labels` only for one to three exact available labels that are missing, incomplete, or clearly wrong.

## Tool Rules
- Only `issues_search` and `documents_search` are permitted.
- For `Bug` or `Feature`, call `issues_search` first.
- For `Question`, call `documents_search` first.
- Use the other search tool only when the first leaves material decision uncertainty.
- Use distinctive symptoms, version, product area, or requested capability in non-duplicate queries.
- Refine a follow-up query only to resolve a stated gap by adding a distinctive symptom, version, affected component, exact error, or configuration term. Do not repeat the same semantic query with superficial wording changes.
- **The total combined budget is eight tool calls.**
- Stop immediately when evidence supports a concrete outcome.
- Also stop after two consecutive searches with no material evidence gain, at budget exhaustion, or when further search cannot change the choice.
- **Never call `get_record`, suggestion tools, or any undeclared tool.**

## Required Output
- Return the required Analysis JSON: `analysis_report`, `recommended_actions`, `original_issue_type`, `classified_issue_type`, and `skip_handling`.
- Set `original_issue_type` to the exact supplied value or an empty string if it is absent, and set `classified_issue_type` to `Bug`, `Feature`, or `Question`. Never include `suggest_modify_type` unless those fields support the proposed change.
- Place exact returned labels adjacent to each supported claim, for example `<reference_0>`.
- Never invent, alter, merge, or retain non-decisive reference labels.
- Do not return record identities, `reference_related_records`, or a separate reference-label list; the framework validates report citations and resolves applicable Handling records.
- **Make `no_op` exclusive if used.**
- Set `skip_handling: false` for actionable recommendations and true only for explicit `no_op`.
- Explain classification, evidence, omissions, and uncertainty in `analysis_report`.

## Forbidden Behavior
- Do not call `get_record` or any suggestion tool.
- Do not search with secrets, private contact data, credentials, tokens, or unredacted sensitive content.
- Do not present a weak match as a duplicate or direct answer.
- Do not invent labels or propose labels outside observed `available_labels`.
- Do not change issue type merely because a title contains a familiar keyword.
- **Do not claim that any recommendation has already been applied.**

## Examples
- `Bug`: “Export fails after 3.2,” including when phrased as “Why does export fail?”
- `Feature`: “Can I bulk archive projects?” when bulk archive is requested but unavailable.
- `Question`: “How do I configure SSO?” when it requests documented configuration.
