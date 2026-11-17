from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

import httpx
import pytest

from backend.agent import select_tools
from backend.app import create_app
from backend.config import SettingsStore, protect
from backend.knowledge import KnowledgeBase, extract, split_text, terms
from backend.storage import Database


@pytest.fixture
def store(tmp_path):
    return Database(tmp_path / 'test.db')


@pytest.fixture
def config(tmp_path):
    return SettingsStore(tmp_path / 'settings.json')


@pytest.fixture
def app(store, config):
    return create_app(store, config, token='test-token')


@pytest.fixture
def client(app):
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://testserver',
                             headers={'Authorization': 'Bearer test-token'})


def test_database_sessions_and_cascade(store):
    session = store.create_session()
    message = store.add_message(session['id'], 'user', '创建 100mm 底板')
    store.event(session['id'], {'type': 'status', 'message': 'checking'})
    assert store.sessions()[0]['title'] == message['content']
    assert store.messages(session['id'])[0]['content'] == message['content']
    assert len(store.events(session['id'])) == 1
    store.delete_session(session['id'])
    assert not store.messages(session['id'])
    assert not store.events(session['id'])


def test_context_summary_preserves_original_messages(store):
    session = store.create_session()
    store.add_message(session['id'], 'user', '100 x 60 x 10 mm')
    seq = store.messages(session['id'])[0]['seq']
    store.summarize(session['id'], 'plate: 100 x 60 x 10 mm', seq)
    assert store.session(session['id'])['summary_through'] == seq
    assert len(store.messages(session['id'])) == 1


def test_chunking_and_chinese_terms():
    assert '拉伸' in terms('创建拉伸特征')
    chunks = split_text('A' * 2400)
    assert len(chunks) == 3
    assert chunks[0][-150:] == chunks[1][:150]


def test_html_extraction():
    text = extract('api.html', b'<h1>Extrude</h1><script>evil()</script><p>mm</p>')
    assert 'Extrude' in text and 'evil' not in text
    with pytest.raises(ValueError):
        extract('sample.exe', b'PE')


async def test_lexical_rag(store, config):
    kb = KnowledgeBase(store, config)
    doc = await kb.ingest('建模规范.md', '拉伸底板厚度10毫米，使用草图矩形。'.encode())
    await kb.ingest('其他.md', '铜材料价格和物流采购信息。'.encode())
    found = await kb.search('底板拉伸厚度')
    assert found[0]['name'] == '建模规范.md'
    assert found[0]['document_id'] == doc['id']
    store.delete_document(doc['id'])
    assert not [c for c in store.chunks() if c['document_id'] == doc['id']]


async def test_semantic_fusion_with_injected_vectors(store, config, monkeypatch):
    config.update({'embedding_model': 'test-vector', 'embedding_base_url': 'http://localhost:1234/v1'})
    kb = KnowledgeBase(store, config)
    store.add_document('API.md', ['Sweep follows a path'], [[1.0, 0.0]], kb.embedding_identity())
    async def embed(texts):
        return [[1.0, 0.0] for _ in texts]
    monkeypatch.setattr(kb, 'embed', embed)
    found = await kb.search('沿路径生成截面')
    assert found[0]['name'] == 'API.md'


def test_tool_discovery_translates_chinese():
    tools = [{'name': 'feature_extrude', 'description': 'Extrude sketch'},
             {'name': 'asm_mate_add', 'description': 'assembly mates'},
             {'name': 'vba_run', 'description': 'extrude arbitrary'}]
    assert select_tools(tools, '拉伸底板')[0]['name'] == 'feature_extrude'
    assert all(t['name'] != 'vba_run' for t in select_tools(tools, 'extrude'))


@pytest.mark.skipif(sys.platform != 'win32', reason='Windows DPAPI')
def test_credentials_encrypted_and_preserved(config):
    config.update({'api_key': 'sk-unit-secret'})
    assert 'sk-unit-secret' not in config.path.read_text('utf-8')
    assert config.key() == 'sk-unit-secret'
    config.update({'api_key': '', 'model': 'test-model'})
    assert config.key() == 'sk-unit-secret'
    assert 'api_key' not in config.public()
    assert config.public()['has_api_key']
    loaded = SettingsStore(config.path)
    assert loaded.key() == 'sk-unit-secret'
    config.update({'clear_api_key': True})
    assert not config.encrypted_key


def test_bad_url_does_not_change_settings(config):
    with pytest.raises(ValueError):
        config.update({'base_url': 'file:///etc/passwd'})
    assert config.value.base_url.startswith('https://')


async def test_auth_and_host(app):
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://testserver') as raw:
        assert (await raw.get('/api/settings')).status_code == 401
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://evil.example',
                                headers={'Authorization': 'Bearer test-token'}) as raw:
        assert (await raw.get('/api/health')).status_code == 400


async def test_health_is_not_fake_success(client):
    async with client:
        result = (await client.get('/api/health')).json()
        assert result['status'] == 'ok'
        assert result['solidworks']['connected'] is False
        assert result['mcp']['tools'] == 0


async def test_session_api(client):
    async with client:
        session = (await client.post('/api/sessions', json={})).json()
        assert (await client.get('/api/sessions')).json()[0]['id'] == session['id']
        assert (await client.get(f"/api/sessions/{session['id']}/messages")).json() == []
        assert (await client.delete(f"/api/sessions/{session['id']}")).status_code == 200
        assert (await client.get(f"/api/sessions/{session['id']}/messages")).status_code == 404


async def test_knowledge_upload(client):
    async with client:
        result = await client.post('/api/knowledge', files={'file': ('api.md', b'Extrude sketch depth in mm')})
        assert result.status_code == 200
        assert result.json()['chunks'] == 1
        assert len((await client.get('/api/knowledge')).json()) == 1
        bad = await client.post('/api/knowledge', files={'file': ('bad.exe', b'PE')})
        assert bad.status_code == 400


async def test_missing_mcp_is_actionable(client, config):
    # The default now discovers a bundled executor; explicitly exercise an empty configuration.
    config.value.mcp_command = ''
    async with client:
        result = await client.post('/api/mcp/connect')
        assert result.status_code == 400
        assert 'solidworks-mcp.exe' in result.json()['detail']


async def test_settings_never_return_key(client):
    async with client:
        response = await client.put('/api/settings', json={'model': 'example-model', 'mcp_args': ['--example']})
        assert response.status_code == 200
        assert 'api_key' not in response.json()
        assert response.json()['model'] == 'example-model'


async def test_chat_stream_and_persistence(client, app, config, store, monkeypatch):
    monkeypatch.setenv('FORMA_API_KEY', 'fixture-key')
    async def fake_run(session_id, query, emit):
        await emit({'type': 'status', 'message': 'fixture: no CAD execution'})
        await emit({'type': 'token', 'content': 'fixture response'})
        store.add_message(session_id, 'assistant', 'fixture response')
    monkeypatch.setattr(app.state.agent, 'run', fake_run)
    async with client:
        session = (await client.post('/api/sessions', json={})).json()
        result = await client.post('/api/chat', json={'session_id': session['id'], 'message': 'test'})
        assert result.status_code == 200
        events = [json.loads(line[6:]) for line in result.text.splitlines() if line.startswith('data: ')]
        assert events[-1]['type'] == 'done'
        assert any(e.get('content') == 'fixture response' for e in events)
        messages = (await client.get(f"/api/sessions/{session['id']}/messages")).json()
        assert [m['role'] for m in messages] == ['user', 'assistant']


async def test_chat_requires_configuration(client, monkeypatch):
    monkeypatch.delenv('FORMA_API_KEY', raising=False)
    async with client:
        session = (await client.post('/api/sessions', json={})).json()
        result = await client.post('/api/chat', json={'session_id': session['id'], 'message': 'Create plate'})
        assert result.status_code == 400
        assert (await client.get('/api/health')).json()['task_running'] is False


async def test_agent_failure_releases_execution_lock(client, app, monkeypatch):
    monkeypatch.setenv('FORMA_API_KEY', 'fixture-key')
    async def failure(*args):
        raise RuntimeError('fixture-key provider unavailable')
    monkeypatch.setattr(app.state.agent, 'run', failure)
    async with client:
        session = (await client.post('/api/sessions', json={})).json()
        response = await client.post('/api/chat', json={'session_id': session['id'], 'message': 'test'})
        assert '[redacted]' in response.text
        assert 'fixture-key' not in response.text
        assert (await client.get('/api/health')).json()['task_running'] is False
