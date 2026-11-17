"""Saved explicit limits survive the new dsh defaults without startup writes."""
import json

import pytest

from backend.config import SettingsStore
from backend.model_options import ModelOptions, stored_model_options
from backend.model_profiles import ProfileStore


@pytest.mark.parametrize('blank', [None, '', '  '])
@pytest.mark.parametrize('per_model', [False, True])
def test_small_legacy_context_keeps_compatible_inherited_output(tmp_path, blank, per_model):
    values = {'max_output_tokens': blank}
    if per_model:
        values['model_capabilities'] = {'fixture': {'context_window': 64000, 'vision': True}}
    else:
        values['context_window'] = 200000
    path = tmp_path / 'settings.json'
    path.write_text(json.dumps(values), encoding='utf-8')
    before = path.read_bytes()
    settings = SettingsStore(path)
    assert settings.value.max_output_tokens == 8192
    assert path.read_bytes() == before
    assert ModelOptions(**stored_model_options(values)).max_output_tokens == 8192
    assert values['max_output_tokens'] == blank


@pytest.mark.parametrize('output', [8192, 32768, 256000])
def test_saved_explicit_output_is_preserved(tmp_path, output):
    path = tmp_path / 'settings.json'
    path.write_text(json.dumps({'context_window': 1000000, 'max_output_tokens': output}), encoding='utf-8')
    before = path.read_bytes()
    assert SettingsStore(path).value.max_output_tokens == output
    assert path.read_bytes() == before


def test_profile_migration_preserves_selection_credentials_and_capabilities(tmp_path, monkeypatch):
    monkeypatch.setenv('FORMA_API_KEY', 'fixture-secret')
    store = ProfileStore(SettingsStore(tmp_path / 'settings.json'))
    payload = {'selected_id': 'fixture', 'selected_model': 'm', 'profiles': [{
        'id': 'fixture', 'name': 'Fixture', 'models': ['m'], 'base_url': 'https://provider.test/v1',
        'full_endpoint': False, 'env_key': True, 'context_window': 200000,
        'model_capabilities': {'m': {'vision': True, 'reasoning_efforts': ['high']}}}]}
    store.path.write_text(json.dumps(payload), encoding='utf-8')
    before = store.path.read_bytes()
    config, key, profile = store.resolve()
    assert config.max_output_tokens == 8192 and config.context_window == 200000
    assert key == 'fixture-secret' and profile['id'] == 'fixture'
    assert config.model_capabilities['m'].vision is True
    assert store.public()['selected_model'] == 'm'
    assert store.path.read_bytes() == before


def test_absent_limits_use_dsh_defaults():
    assert ModelOptions(**stored_model_options({})).max_output_tokens == 256000
    assert ModelOptions(**stored_model_options({})).context_window == 1000000
