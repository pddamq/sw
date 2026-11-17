"""Offline persistence and validation of configurable model transport limits."""
import pytest
from pydantic import ValidationError
from backend.config import Settings, SettingsStore
from backend.model_profiles import ProfileStore


@pytest.mark.parametrize('changes', [
    {'model_max_retries': -1}, {'model_max_retries': 11}, {'model_max_retries': True},
    {'model_retry_initial_seconds': 0}, {'model_retry_initial_seconds': 31},
    {'model_retry_max_seconds': 301}, {'model_retry_jitter': -0.1},
    {'model_retry_jitter': 1.1}, {'model_idle_timeout_seconds': 0},
    {'model_idle_timeout_seconds': 1801}, {'model_retry_jitter': float('nan')},
])
def test_invalid_retry_configuration_is_rejected(changes):
    with pytest.raises(ValidationError):
        Settings(**changes)


def test_retry_settings_roundtrip_and_apply_to_resolved_profiles(tmp_path):
    store = SettingsStore(tmp_path / 'settings.json')
    store.update({'model_max_retries': 0, 'model_retry_initial_seconds': 2,
                  'model_retry_max_seconds': 15, 'model_retry_jitter': 0,
                  'model_idle_timeout_seconds': 45})
    restored = SettingsStore(store.path)
    config, key, profile = ProfileStore(restored).resolve()
    assert config.model_max_retries == 0
    assert config.model_retry_initial_seconds == 2
    assert config.model_retry_max_seconds == 15
    assert config.model_retry_jitter == 0
    assert config.model_idle_timeout_seconds == 45
    assert restored.public()['model_max_retries'] == 0
