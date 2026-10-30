"""Every shipped golden case must be well formed. These tests never call a model."""
from pathlib import Path

import pytest
import yaml

from seaqa_ai.agent.utils import get_skill_map

from tests.agent.support.harness import load_case, prepare_case

CASES_DIR = Path(__file__).resolve().parent / 'cases'
CASE_DIRS = sorted(path for path in CASES_DIR.glob('*/*') if path.is_dir())


def test_there_are_golden_cases():
    assert CASE_DIRS


@pytest.mark.parametrize('case_dir', CASE_DIRS, ids=lambda path: f'{path.parent.name}/{path.name}')
def test_case_is_complete_and_matches_its_event_folder(case_dir):
    case = load_case(case_dir)

    assert (case_dir / 'expected.yaml').is_file()
    assert (case_dir / 'reference.yaml').is_file()
    assert case['task']['event']['type'] == case_dir.parent.name
    assert case_dir.parent.name in get_skill_map()
    assert case['expected']['suggestions']
    yaml.safe_load((case_dir / 'reference.yaml').read_text(encoding='utf-8'))


@pytest.mark.parametrize('case_dir', CASE_DIRS, ids=lambda path: f'{path.parent.name}/{path.name}')
def test_case_prepares_like_a_production_run(case_dir):
    """The trigger and linked records load through the production preparation path."""
    case = load_case(case_dir)

    task, _app, prepared = prepare_case(case)

    assert prepared.trigger_record['title']
    assert task.trigger_event.event_type == case_dir.parent.name
