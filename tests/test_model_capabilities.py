import pytest
from pydantic import ValidationError
from backend.config import Settings, SettingsStore
from backend.model_profiles import ProfileStore, ProfileDraft
from backend.model_probe import ModelDraft, draft_connection
from backend.model_capabilities import ModelCapability, apply_capabilities, validate_effort


def test_unknown_capabilities_keep_manual_behavior():
    original = Settings(model='custom', vision_enabled=True)
    effective = apply_capabilities(original)
    validate_effort(effective, 'high')
    assert effective.vision_enabled and effective.context_window == original.context_window


def test_overrides_apply_without_changing_saved_defaults():
    original = Settings(model='custom', vision_enabled=True, model_capabilities={'custom': {
        'reasoning_efforts': ['low', 'high'], 'vision': False, 'context_window': 32000, 'max_output_tokens': 4000}})
    effective = apply_capabilities(original)
    assert not effective.vision_enabled
    assert effective.context_window == 32000 and effective.max_output_tokens == 4000
    assert original.context_window == 1000000 and original.vision_enabled
    with pytest.raises(ValueError, match='思考强度'):
        validate_effort(effective, 'medium')
    validate_effort(effective, None)
    validate_effort(effective, 'low')


@pytest.mark.parametrize('value', [{'vision': 'yes'}, {'context_window': True}, {'max_output_tokens': 0},
                                   {'reasoning_efforts': ['invented']}, {'context_window': 10, 'max_output_tokens': 10}])
def test_invalid_capabilities_rejected(value):
    with pytest.raises(ValidationError):
        ModelCapability(**value)


def test_profile_and_draft_roundtrip_preserve_capabilities_and_secrets(tmp_path, monkeypatch):
    monkeypatch.setenv('FORMA_API_KEY', 'fixture-only')
    settings = SettingsStore(tmp_path / 'settings.json')
    profiles = ProfileStore(settings)
    draft = ProfileDraft(id='legacy', name='fixture', base_url=settings.value.base_url,
                         models=[settings.value.model], model_capabilities={settings.value.model: {
                             'reasoning_efforts': [], 'context_window': 50000, 'max_output_tokens': 2000}})
    public = profiles.save(draft)
    assert 'fixture-only' not in str(public)
    config, key, profile = profiles.resolve()
    assert config.context_window == 50000 and key == 'fixture-only'
    with pytest.raises(ValueError):
        validate_effort(config, 'low')
    transient, transient_key = draft_connection(settings, profiles, ModelDraft(
        profile_id='legacy', base_url=config.base_url, model=config.model, max_output_tokens=8192,
        model_capabilities={config.model: {'context_window': 60000}}))
    assert transient.context_window == 60000 and transient_key == key
    assert profiles.resolve()[0].context_window == 50000


def test_deepseek_messages_medium_rejected_before_network():
    with pytest.raises(ValueError, match='low'):
        validate_effort(Settings(protocol='claude', base_url='https://api.deepseek.com/anthropic'), 'medium')
