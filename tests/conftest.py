"""Test bootstrap. Must run before any seaqa_ai import.

Hermetic-test rules for everything that is not marked ``llm``:

1. Credential-shaped environment variables are removed.
2. The timezone is fixed to UTC.
3. Any attempt to reach a real model (completion, responses, embedding) raises.
"""
import os
from pathlib import Path

import pytest

_TESTS_DIR = Path(__file__).resolve().parent

if not os.environ.get('CONF_PATH'):
    os.environ['CONF_PATH'] = str(_TESTS_DIR)
    os.environ['SEAQA_CONFIG_NAME'] = 'placeholder_seaqa_config.yaml'


_CREDENTIAL_SUFFIXES = (
    '_API_KEY', '_TOKEN', '_SECRET', '_PASSWORD', '_CREDENTIALS', '_ACCESS_KEY',
    '_SECRET_ACCESS_KEY', '_PRIVATE_KEY',
)
_MODEL_REQUEST_FUNCTIONS = (
    'litellm.responses',
    'litellm.completion',
    'litellm.acompletion',
    'litellm.embedding',
)


def pytest_collection_modifyitems(config, items):
    if os.environ.get('RUN_LLM_TESTS') == '1':
        return
    skip_llm = pytest.mark.skip(
        reason='Set RUN_LLM_TESTS=1 to run tests that call a real model',
    )
    for item in items:
        if 'llm' in item.keywords:
            item.add_marker(skip_llm)


def _blocked_model_request(*args, **kwargs):
    raise AssertionError(
        'A test that is not marked "llm" tried to call a real model. '
        'Pass a ScriptedResponsesClient instead.'
    )


@pytest.fixture(autouse=True)
def _hermetic_environment(request, monkeypatch):
    if 'llm' in request.keywords:
        return
    for name in list(os.environ):
        if name.endswith(_CREDENTIAL_SUFFIXES):
            monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv('TZ', 'UTC')
    for target in _MODEL_REQUEST_FUNCTIONS:
        monkeypatch.setattr(target, _blocked_model_request, raising=False)
