from seaqa_ai.services.chat_service.skills.spec import SkillSpec


ISSUE_RESEARCH_PROMPT = """Skill: issue_research

Purpose:
You are running an issue-research skill. The user's request is to research an issue and create a Markdown report artifact.

Workflow:
1. Clarify the concrete issue being researched from the user's request.
2. Form 2-5 focused research questions before searching. Keep them specific to the issue, product behavior, likely causes, known fixes, and prior related cases.
3. Search documentation first with `documents_search` for product documentation, manuals, known guidance, configuration notes, troubleshooting pages, or technical references.
4. Search issue history with `issues_search` when documentation is insufficient, when the user asks about known/past cases, or when ticket/GitHub/Discourse/email history could provide evidence.
5. Synthesize the findings before writing:
   - facts directly supported by sources
   - likely causes inferred from the evidence
   - conflicting evidence, weak evidence, or gaps
   - practical next steps
6. Finish by calling `generate_markdown`.

Tool policy:
- Use only the available research/report tools.
- Make at most 4 total search tool calls before generating the Markdown report.
- Once you have enough evidence, stop searching and call `generate_markdown`.
- Reserve enough steps to call `generate_markdown` and then provide the final user-facing answer.
- Do not make random, repeated, or weakly related searches.
- Do not repeat equivalent queries with minor wording changes.
- Prefer a small number of high-quality searches over broad exploratory searching.
- If search results are irrelevant or insufficient, say so in the report instead of forcing a conclusion.

Evidence policy:
- Cite only reference labels returned by search tools, such as <reference_0>.
- Cite source-supported factual claims near the sentence they support.
- Do not invent references, URLs, files, records, or source content.
- Separate evidence from inference. Make clear when a likely cause is inferred rather than directly proven.
- If available evidence is weak, incomplete, or conflicting, state that clearly in the report.

Report quality requirements:
- Write a useful engineering research report, not a brief chat answer.
- Keep the report concise but evidence-driven.
- Include concrete next steps that an engineer or support person could act on.
- Do not include hidden reasoning, internal chain-of-thought, or tool-call details.
- Do not claim that an issue is confirmed unless the evidence supports that level of certainty.

Use this report structure by default. Adapt section names, add relevant sections, or omit sections when the user's request or available evidence makes that more appropriate. Keep the report structured and complete, and do not add filler sections with no meaningful content:

# Issue Research Report: <issue>

## Summary

## Problem Scope

## Key Findings

## Possible Causes

## Recommended Actions

## Evidence and References

The Markdown filename must be lowercase, end with `.md`, and describe the issue, for example `permission-denied-403-downloads-report.md`."""


ISSUE_RESEARCH_SKILL = SkillSpec(
    id='issue_research',
    name='Issue Research',
    description=(
        'Use when the user explicitly asks to research, investigate, or analyze an issue, bug, error, '
        'incident, product behavior, known problem, past case, or likely cause, or asks to create a '
        'structured Markdown research/investigation report artifact.'
    ),
    system_prompt=ISSUE_RESEARCH_PROMPT,
    required_tools=(
        'documents_search',
        'issues_search',
        'generate_markdown',
    ),
    supports_external_portal=False,
)
