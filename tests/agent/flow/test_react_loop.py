"""The ReACT loop inside each phase: step limit, output repair, tool errors, references."""
import json

from seaqa_ai.agent.agent_service.phase_promoters import ALL_PHASES

from tests.agent.support.builders import (
    analysis_output, identity, prelude_output, search_hit, text, tool_call, tool_calls,
)
from tests.agent.support.calls import (
    error_rows, get_record_calls, ordered_records_prompts, system_messages,
)
from tests.agent.support.fakes import ScriptedResponsesClient
from tests.agent.support.harness import run_case
from tests.agent.support.scenarios import CANDIDATE_TICKET_ID, TRIGGER_ID, scenario

EVENT = 'github_issue_added'
TRIGGER = ('github_issue', 1, TRIGGER_ID)


def _tool_outputs(call):
    return [
        json.loads(item['output']) for item in call['input'] or []
        if isinstance(item, dict) and item.get('type') == 'function_call_output'
        and item['output'].startswith('{')
    ]


def test_tools_are_disabled_once_the_step_limit_is_reached():
    s = scenario(EVENT)
    spec = ALL_PHASES['prelude']
    reload_trigger = tool_calls(tool_call(
        'get_record', source_type='github_issue', connection_id=1, record_id=TRIGGER_ID,
    ))
    client = ScriptedResponsesClient([
        *[reload_trigger] * spec.max_steps,
        prelude_output(TRIGGER),
        analysis_output(EVENT, ['no_op']),
    ])

    run = run_case(s.case, responses_client=client)

    last_tool_step = spec.max_steps - 1
    assert spec.max_steps_prompt not in system_messages(client.calls[last_tool_step - 1])
    assert spec.max_steps_prompt in system_messages(client.calls[last_tool_step])
    final_prelude_call = client.calls[spec.max_steps]
    assert final_prelude_call['tools'] == []
    assert final_prelude_call['tool_choice'] == 'none'
    # The tool call made at the last step is answered with an error, not executed.
    assert any(
        spec.max_steps_prompt in row['result'] for row in error_rows(run.tracer_rows, 'prelude')
    )
    assert run.phases['prelude']['event_summary']


def test_final_output_that_breaks_the_schema_is_repaired_without_tools():
    s = scenario(EVENT)
    client = ScriptedResponsesClient([
        prelude_output(TRIGGER),
        text(json.dumps({'analysis_report': 'A report with nothing else.'})),
        analysis_output(EVENT, ['no_op']),
    ])

    run = run_case(s.case, responses_client=client)

    repair_call = client.calls[2]
    assert repair_call['tools'] == []
    assert any(
        'did not match the required JSON schema' in message
        for message in system_messages(repair_call)
    )
    assert len(error_rows(run.tracer_rows, 'analysis')) == 1
    assert run.phases['analysis']['recommended_actions'] == ['no_op']


def test_a_failing_tool_call_is_reported_to_the_model_and_the_run_continues():
    s = scenario(EVENT)
    client = ScriptedResponsesClient([
        prelude_output(TRIGGER),
        analysis_output(EVENT, ['suggest_assign_labels'], suggested_labels=['not-a-label']),
        tool_calls(tool_call(
            'suggest_assign_labels', suggested_labels=['not-a-label'], reason='Looks like a bug.',
        )),
        text('The label suggestion could not be created.'),
    ])

    run = run_case(s.case, responses_client=client)

    assert run.suggestions == []
    errors = error_rows(run.tracer_rows, 'handling')
    assert [row['tool_name'] for row in errors] == ['suggest_assign_labels']
    observation = _tool_outputs(client.calls[-1])[-1]
    assert observation['status'] == 'error'
    assert observation['error_code'] == 'invalid_input'
    assert observation['retryable'] is False
    assert run.phases['handling']


def test_report_citations_become_records_that_handling_receives():
    s = scenario(EVENT)
    s.case['search_results'] = {'issues_search': [search_hit(
        'ticket', CANDIDATE_TICKET_ID, title='A candidate ticket',
        content='The menu bar click crashes the client.',
    )]}
    client = ScriptedResponsesClient([
        prelude_output(TRIGGER),
        tool_calls(tool_call('issues_search', query='client crashes when clicking the menu bar icon')),
        analysis_output(
            EVENT, ['suggest_create_ticket'],
            report='A related ticket exists <reference_0> and the report is a crash.',
        ),
        text('Nothing to do.'),
    ])

    run = run_case(s.case, responses_client=client)

    analysis = run.phases['analysis']
    assert analysis['reference_related_records'] == [identity('ticket', None, CANDIDATE_TICKET_ID)]
    assert '<reference_0>' not in analysis['analysis_report']
    assert [call['tool'] for call in run.search_calls] == ['issues_search']
    handling_call = client.calls[-1]
    assert any(
        prompt.startswith('PreStep 2: Inject records related to the analysis report')
        for prompt in ordered_records_prompts(handling_call)
    )
    assert get_record_calls(handling_call)[-1] == identity('ticket', None, CANDIDATE_TICKET_ID)


def test_a_citation_without_a_search_result_is_removed():
    s = scenario(EVENT)
    client = ScriptedResponsesClient([
        prelude_output(TRIGGER),
        analysis_output(
            EVENT, ['suggest_create_ticket'], report='Per <reference_5> this is known.',
        ),
        text('Nothing to do.'),
    ])

    run = run_case(s.case, responses_client=client)

    analysis = run.phases['analysis']
    assert '<reference_5>' not in analysis['analysis_report']
    assert analysis['reference_related_records'] == []
