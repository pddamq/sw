import json

import pytest
from pydantic import ValidationError

from backend.config import Settings, SettingsStore
from backend.model_probe import ModelDraft, draft_connection
from backend.model_profiles import ProfileDraft, ProfileStore


@pytest.mark.parametrize('protocol', ['chat', 'responses', 'claude'])
def test_protocol_persist_resolve_draft_and_restart(tmp_path, monkeypatch, protocol):
    monkeypatch.setenv('FORMA_API_KEY', 'fixture-key')
    settings = SettingsStore(tmp_path / 'settings.json')
    profiles = ProfileStore(settings)
    saved = profiles.save(ProfileDraft(name='Provider', protocol=protocol, base_url='https://provider.test/v1',
                                       models=['fixture'], api_key='fixture-key'))
    profile = saved['profiles'][-1]
    assert profile['protocol'] == protocol
    profiles.select(profile['id'], 'fixture')
    restarted = ProfileStore(SettingsStore(settings.path))
    config, key, _ = restarted.resolve()
    assert config.protocol == protocol and key == 'fixture-key'
    before = profiles.path.read_bytes()
    config, key = draft_connection(settings, profiles, ModelDraft(profile_id=profile['id'],
        protocol=protocol, base_url='https://provider.test/v1', model='fixture'))
    assert config.protocol == protocol and key == 'fixture-key'
    assert profiles.path.read_bytes() == before
    # Protocol changes at the same service retain the key, without changing URLs.
    profiles.save(ProfileDraft(id=profile['id'], name='Provider', protocol='claude',
                              base_url=profile['base_url'], models=['fixture']))
    assert profiles.resolve()[0].protocol == 'claude'
    assert profiles.resolve()[1] == 'fixture-key'


def test_legacy_profiles_default_chat_without_rewriting(tmp_path, monkeypatch):
    monkeypatch.setenv('FORMA_API_KEY', 'fixture-key')
    settings = SettingsStore(tmp_path / 'settings.json')
    settings.value.protocol = 'claude'
    profiles = ProfileStore(settings)
    assert profiles.public()['profiles'][0]['protocol'] == 'claude'
    profiles.path.write_text(json.dumps({'profiles': [{'id': 'old', 'name': 'Old service',
        'base_url': 'https://provider.test/v1', 'full_endpoint': False, 'models': ['fixture'], 'env_key': True}],
        'selected_id': 'old', 'selected_model': 'fixture'}), 'utf-8')
    before = profiles.path.read_bytes()
    assert profiles.public()['profiles'][0]['protocol'] == 'chat'
    assert profiles.resolve()[0].protocol == 'chat'
    assert profiles.path.read_bytes() == before


@pytest.mark.parametrize('cls,kwargs', [(Settings, {}), (ModelDraft, {'base_url': 'https://provider.test'}),
    (ProfileDraft, {'name': 'Provider', 'base_url': 'https://provider.test', 'models': ['fixture']})])
def test_invalid_protocol_rejected(cls, kwargs):
    with pytest.raises(ValidationError):
        cls(protocol='invented', **kwargs)
