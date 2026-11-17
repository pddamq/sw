import asyncio

import httpx

from backend.app import create_app
from backend.config import SettingsStore
from backend.storage import Database


async def test_only_one_execution_and_settings_locked(tmp_path, monkeypatch):
    monkeypatch.setenv('FORMA_API_KEY', 'fixture-key')
    app = create_app(Database(tmp_path / 'concurrent.db'), SettingsStore(tmp_path / 'settings.json'), token='fixture')
    started, release = asyncio.Event(), asyncio.Event()
    async def fake_run(session_id, query, emit):
        started.set()
        await release.wait()
        await emit({'type': 'token', 'content': 'finished fixture'})
    monkeypatch.setattr(app.state.agent, 'run', fake_run)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url='http://testserver',
                                headers={'Authorization': 'Bearer fixture'}) as client:
        session = (await client.post('/api/sessions', json={})).json()
        payload = {'session_id': session['id'], 'message': 'test'}
        first = asyncio.create_task(client.post('/api/chat', json=payload))
        await asyncio.wait_for(started.wait(), 2)
        try:
            assert (await client.post('/api/chat', json=payload)).status_code == 409
            assert (await client.put('/api/settings', json={'model': 'other'})).status_code == 409
            assert (await client.post('/api/mcp/disconnect')).status_code == 409
            assert (await client.delete(f"/api/sessions/{session['id']}")).status_code == 409
        finally:
            release.set()
            await first
        assert (await client.get('/api/health')).json()['task_running'] is False


async def test_cancel_endpoint_releases_lock(tmp_path, monkeypatch):
    monkeypatch.setenv('FORMA_API_KEY', 'fixture-key')
    app = create_app(Database(tmp_path / 'cancel.db'), SettingsStore(tmp_path / 'settings.json'), token='fixture')
    started = asyncio.Event()
    async def fake_run(session_id, query, emit):
        started.set()
        await asyncio.Event().wait()
    monkeypatch.setattr(app.state.agent, 'run', fake_run)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url='http://testserver',
                                headers={'Authorization': 'Bearer fixture'}) as client:
        session = (await client.post('/api/sessions', json={})).json()
        pending = asyncio.create_task(client.post('/api/chat', json={'session_id': session['id'], 'message': 'test'}))
        await asyncio.wait_for(started.wait(), 2)
        assert (await client.post('/api/tasks/cancel')).status_code == 200
        response = await asyncio.wait_for(pending, 2)
        assert '任务已停止' in response.text
        assert (await client.get('/api/health')).json()['task_running'] is False


async def test_dev_cors_preflight_without_token(tmp_path):
    app = create_app(Database(tmp_path / 'cors.db'), SettingsStore(tmp_path / 'settings.json'), token='fixture')
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url='http://testserver') as client:
        response = await client.options('/api/chat', headers={
            'Origin': 'http://localhost:52881', 'Access-Control-Request-Method': 'POST',
            'Access-Control-Request-Headers': 'authorization,content-type'})
        assert response.status_code == 200
        assert response.headers['access-control-allow-origin'] == 'http://localhost:52881'
        response = await client.options('/api/chat', headers={
            'Origin': 'http://evil.example', 'Access-Control-Request-Method': 'POST'})
        assert response.status_code == 400
