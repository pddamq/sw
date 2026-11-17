import json

import httpx
import pytest
from openai import AsyncOpenAI

from backend.app import create_app
from backend.config import Settings, SettingsStore
from backend.model_endpoint import api_root, chat_endpoint, client_options, models_endpoint
from backend.model_profiles import ProfileDraft, ProfileStore
from backend.model_probe import ModelDraft, draft_connection, check_model, list_models
from backend.storage import Database
from test_agent import FakeBridge


@pytest.fixture
def store(tmp_path, monkeypatch):
    monkeypatch.setenv('FORMA_API_KEY', 'legacy-secret')
    settings = SettingsStore(tmp_path / 'settings.json')
    return settings, ProfileStore(settings)


def draft(**changes):
    return ProfileDraft(**{'name': 'Second service', 'base_url': 'https://second.test/v1',
                          'models': ['model-a', 'model-b'], 'api_key': 'second-secret', **changes})


def test_profiles_migrate_preserve_separate_keys_and_selection(store):
    settings, profiles = store
    assert profiles.public()['selected_id'] == 'legacy'
    result = profiles.save(draft())
    id = result['profiles'][-1]['id']
    assert len(result['profiles']) == 2
    assert 'second-secret' not in json.dumps(result)
    assert 'legacy-secret' not in profiles.path.read_text('utf-8')
    assert 'second-secret' not in profiles.path.read_text('utf-8')
    profiles.select(id, 'model-b')
    restarted = ProfileStore(SettingsStore(settings.path))
    config, key, _ = restarted.resolve()
    assert (config.model, config.base_url, key) == ('model-b', 'https://second.test/v1', 'second-secret')
    assert restarted.resolve('legacy')[1] == 'legacy-secret'
    profiles.save(draft(id=id, name='Renamed', api_key=''))
    assert profiles.resolve()[1] == 'second-secret'
    with pytest.raises(ValueError):
        profiles.resolve(id, 'not-enabled')
    with pytest.raises(ValueError):
        profiles.save(draft(id=id, base_url='https://different.test', api_key=''))
    profiles.delete(id)
    assert profiles.public()['selected_id'] == 'legacy'
    profiles.delete('legacy')
    assert profiles.public()['profiles'] == []
    with pytest.raises(ValueError):
        profiles.resolve()


def test_draft_tests_never_save_or_reuse_other_services_key(store):
    settings, profiles = store
    profile = profiles.save(draft())['profiles'][-1]
    before = profiles.path.read_bytes()
    value = ModelDraft(profile_id=profile['id'], base_url=profile['base_url'], model='model-b')
    assert draft_connection(settings, profiles, value)[1] == 'second-secret'
    value.base_url = 'https://other.test'
    with pytest.raises(ValueError, match='地址'):
        draft_connection(settings, profiles, value)
    value.api_key = 'draft-only'
    assert draft_connection(settings, profiles, value)[1] == 'draft-only'
    assert profiles.path.read_bytes() == before
    assert not settings.path.exists()


@pytest.mark.parametrize('url,full,expected,models', [
    ('https://example.test/v1/', False, 'https://example.test/v1/chat/completions', 'https://example.test/v1/models'),
    ('https://example.test/v1/chat/completions', True, 'https://example.test/v1/chat/completions', 'https://example.test/v1/models'),
    ('https://example.test/custom/respond/', True, 'https://example.test/custom/respond/', 'https://example.test/custom/models'),
])
async def test_exact_endpoint_used_by_real_openai_client(url, full, expected, models):
    config = Settings(base_url=url, full_endpoint=full)
    assert chat_endpoint(config) == expected and models_endpoint(config) == models
    opts = client_options(config)
    original = opts.pop('http_client', None)
    hooks = original.event_hooks if original else {}
    if original:
        await original.aclose()
    observed = []
    def respond(request):
        observed.append((str(request.url), request.headers['authorization']))
        return httpx.Response(200, json={'id': 'fixture', 'object': 'chat.completion', 'created': 0,
                             'model': 'm', 'choices': []})
    opts['http_client'] = httpx.AsyncClient(transport=httpx.MockTransport(respond), event_hooks=hooks)
    async with AsyncOpenAI(api_key='selected-key', **opts) as client:
        await client.chat.completions.create(model='m', messages=[{'role': 'user', 'content': 'test'}])
    assert observed == [(expected, 'Bearer selected-key')]
    assert '/chat/completions' not in api_root(config)


def sse(usage=True, complete=True):
    rows = [dict(id='test', object='chat.completion.chunk', created=0, model='m',
                 choices=[dict(index=0, delta={'content': 'Connection test output.'}, finish_reason=None)])]
    if complete:
        rows.append({**rows[0], 'choices': [dict(index=0, delta={}, finish_reason='stop')]})
    if usage:
        rows.append({**rows[0], 'choices': [], 'usage': {'prompt_tokens': 10, 'completion_tokens': 24, 'total_tokens': 34}})
    return ''.join('data: ' + json.dumps(row) + '\n\n' for row in rows) + 'data: [DONE]\n\n'


@pytest.mark.parametrize('usage,complete', [(True, True), (False, True), (True, False)])
async def test_speed_probe_real_stream_usage_and_incomplete(monkeypatch, usage, complete):
    import backend.model_probe as module
    observed = []
    def respond(request):
        observed.append(request)
        return httpx.Response(200, text=sse(usage, complete), headers={'content-type': 'text/event-stream'})
    def options(config, **kw):
        return {'base_url': config.base_url, 'max_retries': 0,
                'http_client': httpx.AsyncClient(transport=httpx.MockTransport(respond))}
    monkeypatch.setattr(module, 'client_options', options)
    if not complete:
        with pytest.raises(ValueError, match='中断'):
            await check_model(config=Settings(), key='fixture')
    else:
        result = await check_model(config=Settings(), key='fixture')
        assert result['connected'] and result['tokens_per_second'] > 0
        assert result['token_source'] == ('usage' if usage else 'estimated')
        assert result['first_token_ms'] >= 0 and result['output_tokens'] > 0
        if usage:
            assert result['output_tokens'] == 24
    assert len(observed) == 1


async def test_probe_usage_option_falls_back_only_for_unsupported_option(monkeypatch):
    import backend.model_probe as module
    bodies = []
    def respond(request):
        body = json.loads(request.content)
        bodies.append(body)
        if 'stream_options' in body:
            return httpx.Response(400, json={'error': {'message': 'Unsupported stream_options', 'type': 'invalid_request_error'}})
        return httpx.Response(200, text=sse(False), headers={'content-type': 'text/event-stream'})
    monkeypatch.setattr(module, 'client_options', lambda config, **kw: {
        'base_url': config.base_url, 'max_retries': 0,
        'http_client': httpx.AsyncClient(transport=httpx.MockTransport(respond))})
    result = await check_model(config=Settings(), key='fixture')
    assert len(bodies) == 2 and result['usage_supported'] is False
    assert result['token_source'] == 'estimated'


async def test_models_catalog_and_auth(monkeypatch):
    import backend.model_probe as module
    original = httpx.AsyncClient
    def respond(request):
        assert str(request.url) == 'https://provider.test/v1/models'
        assert request.headers['authorization'] == 'Bearer catalog-key'
        return httpx.Response(200, json={'data': [{'id': 'b'}, {'id': 'a'}, {'id': 'b'}, {'id': 4}]})
    monkeypatch.setattr(module.httpx, 'AsyncClient', lambda **kw: original(transport=httpx.MockTransport(respond), **kw))
    result = await list_models(Settings(base_url='https://provider.test/v1/chat/completions', full_endpoint=True), 'catalog-key')
    assert result['models'] == ['a', 'b']


async def test_profile_api_auth_lock_draft_and_selected_chat(store, tmp_path, monkeypatch):
    import backend.app as module
    settings, profiles = store
    app = create_app(Database(tmp_path / 'app.db'), settings, FakeBridge(), token='fixture')
    seen = []
    async def probe(**kw):
        seen.append((kw['config'].base_url, kw['key']))
        return {'connected': True}
    monkeypatch.setattr(module, 'check_model', probe)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url='http://testserver') as api:
        assert (await api.get('/api/model/profiles')).status_code == 401
        api.headers['Authorization'] = 'Bearer fixture'
        result = (await api.post('/api/model/profiles', json=draft().model_dump())).json()
        id = result['profiles'][-1]['id']
        selected = await api.put('/api/model/selection', json={'profile_id': id, 'model': 'model-b'})
        assert selected.status_code == 200
        before = profiles.path.read_bytes()
        response = await api.post('/api/model/test', json={'base_url': 'https://draft.test/v1', 'model': 'draft', 'api_key': 'draft-key'})
        assert response.status_code == 200 and seen == [('https://draft.test/v1', 'draft-key')]
        assert profiles.path.read_bytes() == before
        async def run(session_id, query, emit):
            config, key = app.state.agent.runtime_model
            assert config.model == 'model-b' and key == 'second-secret'
            assert app.state.agent.reasoning_effort == 'high'
            blocked = await api.put('/api/model/selection', json={'profile_id': 'legacy', 'model': settings.value.model})
            assert blocked.status_code == 409
        monkeypatch.setattr(app.state.agent, 'run', run)
        session = (await api.post('/api/sessions', json={})).json()
        response = await api.post('/api/chat', json={'session_id': session['id'], 'message': 'fixture', 'profile_id': id, 'model': 'model-b', 'reasoning_effort': 'high'})
        assert response.status_code == 200 and 'model_selected' in response.text and 'second-secret' not in response.text
        assert app.state.agent.runtime_model is None
        assert app.state.agent.reasoning_effort is None
        invalid_effort = await api.post('/api/chat', json={'session_id': session['id'], 'message': 'fixture', 'reasoning_effort': 'extreme'})
        assert invalid_effort.status_code == 422
        invalid = await api.post('/api/chat', json={'session_id': session['id'], 'message': 'fixture', 'profile_id': id, 'model': 'wrong'})
        assert invalid.status_code == 400
