import json

import httpx
import pytest
from openai import AsyncOpenAI
from pydantic import ValidationError

from backend.config import Settings, SettingsStore
from backend.model_endpoint import client_options
from backend.model_options import ModelOptions
from backend.model_probe import ModelDraft, draft_connection, list_models, check_model
from backend.model_profiles import ProfileDraft, ProfileStore


def test_defaults_blank_and_legacy_migration(tmp_path, monkeypatch):
    monkeypatch.setenv('FORMA_API_KEY', 'fixture-key')
    settings = SettingsStore(tmp_path / 'settings.json')
    profiles = ProfileStore(settings)
    config, _, _ = profiles.resolve()
    assert config.context_window == 1_000_000
    assert config.max_output_tokens == 256_000
    assert config.user_agent == ''
    for blank in ['', None, '   ']:
        assert ModelOptions(context_window=blank, max_output_tokens=blank, user_agent='').advanced_values() == config.advanced_values()
    assert profiles.public()['profiles'][0]['context_window'] == 1_000_000
    profiles.select('legacy', config.model)
    data = json.loads(profiles.path.read_text('utf-8'))
    for field in ('context_window', 'max_output_tokens', 'user_agent'):
        data['profiles'][0].pop(field, None)
    profiles.path.write_text(json.dumps(data), 'utf-8')
    assert profiles.resolve()[0].context_window == 1_000_000
    assert profiles.public()['profiles'][0]['max_output_tokens'] == 256_000


@pytest.mark.parametrize('values', [
    {'context_window': 0}, {'context_window': -1}, {'context_window': True},
    {'max_output_tokens': 0}, {'max_output_tokens': 1.5},
    {'context_window': 8192}, {'max_output_tokens': 1_000_000},
    {'user_agent': 'fixture\r\nX-Extra: value'}, {'user_agent': 'fixture\x00'},
    {'user_agent': '\u6d4b\u8bd5'}, {'user_agent': 'a' * 1025},
])
def test_invalid_advanced_values(values):
    with pytest.raises(ValidationError):
        ModelOptions(**values)


def test_independent_persistence_and_unsaved_probe_options(tmp_path, monkeypatch):
    monkeypatch.setenv('FORMA_API_KEY', 'fixture-key')
    settings = SettingsStore(tmp_path / 'settings.json')
    profiles = ProfileStore(settings)
    result = profiles.save(ProfileDraft(name='Fixture', base_url='https://fixture.test/v1', models=['fixture'],
        context_window=128_000, max_output_tokens=4096, user_agent=' FixtureClient/1.0 '))
    profile = result['profiles'][-1]
    config, _, _ = ProfileStore(SettingsStore(settings.path)).resolve(profile['id'])
    assert config.advanced_values() == {'context_window': 128_000, 'max_output_tokens': 4096, 'user_agent': 'FixtureClient/1.0', 'model_capabilities': {}}
    assert profiles.resolve('legacy')[0].context_window == 1_000_000
    before = profiles.path.read_bytes()
    probe, _ = draft_connection(settings, profiles, ModelDraft(base_url=config.base_url, api_key='draft-key',
        context_window=64_000, max_output_tokens=2048, user_agent='Draft/2'))
    assert probe.context_window == 64_000 and probe.max_output_tokens == 2048 and probe.user_agent == 'Draft/2'
    assert profiles.path.read_bytes() == before
    profiles.save(ProfileDraft(**{**profile, 'context_window': '', 'max_output_tokens': '', 'user_agent': ''}))
    assert profiles.resolve(profile['id'])[0].advanced_values() == ModelOptions().advanced_values()


@pytest.mark.parametrize('full', [False, True])
@pytest.mark.parametrize('agent', ['', 'FixtureClient/1.0'])
async def test_user_agent_real_sdk_default_and_custom(full, agent):
    url = 'https://fixture.test/v1/chat/completions' if full else 'https://fixture.test/v1'
    config = Settings(base_url=url, full_endpoint=full, user_agent=agent, max_output_tokens=4096)
    options = client_options(config)
    original = options.pop('http_client', None)
    hooks = original.event_hooks if original else {}
    if original:
        await original.aclose()
    seen = []
    def respond(request):
        seen.append(request)
        return httpx.Response(200, json={'id': 'fixture', 'object': 'chat.completion', 'created': 0, 'model': 'fixture', 'choices': []})
    options['http_client'] = httpx.AsyncClient(transport=httpx.MockTransport(respond), event_hooks=hooks)
    async with AsyncOpenAI(api_key='fixture', **options) as client:
        await client.chat.completions.create(model='fixture', messages=[{'role': 'user', 'content': 'test'}], max_tokens=config.max_output_tokens)
    assert seen[0].headers['user-agent'] == (agent or seen[0].headers['user-agent'])
    if not agent:
        assert 'OpenAI' in seen[0].headers['user-agent']
    assert json.loads(seen[0].content)['max_tokens'] == 4096
    assert str(seen[0].url) == 'https://fixture.test/v1/chat/completions'


async def test_catalog_custom_user_agent(monkeypatch):
    import backend.model_probe as module
    original = httpx.AsyncClient
    def respond(request):
        assert request.headers['user-agent'] == 'Fixture/3'
        assert request.headers['authorization'] == 'Bearer fixture'
        return httpx.Response(200, json={'data': [{'id': 'fixture'}]})
    monkeypatch.setattr(module.httpx, 'AsyncClient', lambda **kw: original(transport=httpx.MockTransport(respond), **kw))
    await list_models(Settings(user_agent='Fixture/3'), 'fixture')


async def test_probe_uses_configured_output_limit(monkeypatch):
    import backend.model_probe as module
    from test_model_services import sse
    seen = []
    def respond(request):
        seen.append(json.loads(request.content))
        return httpx.Response(200, text=sse(), headers={'content-type': 'text/event-stream'})
    monkeypatch.setattr(module, 'client_options', lambda config, **kw: {
        'base_url': config.base_url, 'max_retries': 0,
        'http_client': httpx.AsyncClient(transport=httpx.MockTransport(respond))})
    await check_model(config=Settings(max_output_tokens=2048), key='fixture')
    assert seen[0]['max_tokens'] == 2048
