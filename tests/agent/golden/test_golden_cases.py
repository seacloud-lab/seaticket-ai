"""Prompt regression cases that call the configured model."""
import json
import os
from datetime import datetime
from pathlib import Path

import pytest

from tests.agent.support.matchers import evaluate
from tests.agent.support.harness import case_variables, load_case, run_case


CASES_DIR = Path(__file__).resolve().parent / 'cases'
RUNS_DIR = Path(__file__).resolve().parents[1] / '_runs'


def _case_dirs():
    return sorted(
        path for path in CASES_DIR.glob('*/*')
        if (path / 'input.yaml').is_file() and (path / 'expected.yaml').is_file()
    )


def _jsonable(value):
    if isinstance(value, dict):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(item) for item in value]
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return str(value)


def _write_run(case_name, index, payload):
    stamp = datetime.now().strftime('%Y%m%dT%H%M%S')
    directory = RUNS_DIR / case_name
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f'{stamp}-{index}.json'
    path.write_text(
        json.dumps(_jsonable(payload), ensure_ascii=False, indent=2),
        encoding='utf-8',
    )
    return path


@pytest.mark.llm
@pytest.mark.parametrize('case_dir', _case_dirs(), ids=lambda path: path.name)
def test_golden_case(case_dir):
    case = load_case(case_dir)
    expected = case['expected']
    if os.environ.get('SEAQA_TEST_RUNS'):
        runs = int(os.environ['SEAQA_TEST_RUNS'])
        min_pass = runs
    else:
        runs = int(expected.get('runs') or 1)
        min_pass = int(expected.get('min_pass') or runs)

    variables = case_variables(case)
    passed = 0
    reports = []
    for index in range(1, runs + 1):
        result = run_case(case)
        failures = evaluate(result, expected, variables)
        path = _write_run(case['name'], index, {
            'model_id': result.model_id,
            'failures': failures,
            'phases': result.phases,
            'suggestions': result.suggestions,
            'search_calls': result.search_calls,
            'token_usage': result.token_usage,
            'tracer': result.tracer_rows,
        })
        reports.append(f'run {index} ({path.name}): ' + (
            'passed' if not failures else '; '.join(failures)
        ))
        if not failures:
            passed += 1

    assert passed >= min_pass, (
        f'{case["name"]} passed {passed}/{runs}, need {min_pass}\n' + '\n'.join(reports)
    )
