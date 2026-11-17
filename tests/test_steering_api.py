import asyncio

import httpx
import pytest

from backend.app import create_app
from backend.config import SettingsStore
from backend.storage import Database


@pytest.mark.parametrize('cancel', [False, True])
async def test_live_steering_api_receipt_session_guard_and_settlement(tmp_path, monkeypatch, cancel):
    monkeypatch.setenv('FORMA_API_KEY', 'fixture-key')
    db = Database(tmp_path / 'api.db')
    app = create_app(db, SettingsStore(tmp_path / 'settings.json'), token='fixture')
    started, release = asyncio.Event(), asyncio.Event()

    async def run(session_id, query, emit):
        started.set()
        await release.wait()
        for message in app.state.agent.inbox.claim():
            await emit({'type': 'steering_consumed', 'user_message': message, 'queue_id': message['queue_id']})
        app.state.agent.inbox.close()

    monkeypatch.setattr(app.state.agent, 'run', run)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url='http://testserver',
                                headers={'Authorization': 'Bearer fixture'}) as client:
        sid = (await client.post('/api/sessions', json={})).json()['id']
        other = (await client.post('/api/sessions', json={})).json()['id']
        pending = asyncio.create_task(client.post('/api/chat', json={'session_id': sid, 'message': 'Test'}))
        await asyncio.wait_for(started.wait(), 3)
        try:
            health = (await client.get('/api/health')).json()
            assert health['steering_enabled']
            task_id = health['active_task']['id']
            url = f'/api/tasks/{task_id}/steer'
            payload = {'session_id': sid, 'id': 'q1', 'message': 'New requirement'}
            assert (await client.post(url, json={**payload, 'session_id': other})).status_code == 409
            assert (await client.post(url, json={**payload, 'message': ' '})).status_code == 422
            assert (await client.post(url, json=payload, headers={'Authorization': 'Bearer wrong'})).status_code == 401
            app.state.agent.control.pending = {'id': 'approval'}
            assert (await client.post(url, json=payload)).status_code == 409
            app.state.agent.control.pending = None
            first = await client.post(url, json=payload)
            assert first.status_code == 200
            assert first.json()['input']['status'] == 'pending'
            assert (await client.post(url, json=payload)).json()['input']['id'] == 'q1'
            assert (await client.post(url, json={**payload, 'message': 'Different'})).status_code == 409
            assert len((await client.get(f'/api/sessions/{sid}/steering')).json()) == 1
            assert (await client.post('/api/chat', json={'session_id': sid, 'message': 'Parallel'})).status_code == 409
            if cancel:
                await client.post('/api/tasks/cancel')
            else:
                release.set()
            response = await asyncio.wait_for(pending, 3)
            rows = (await client.get(f'/api/sessions/{sid}/steering')).json()
            assert rows[0]['status'] == ('released' if cancel else 'consumed')
            assert ('steering_consumed' in response.text) == (not cancel)
            assert (await client.post(url, json={**payload, 'id': 'late'})).status_code == 409
            assert (await client.post(url, json=payload)).status_code == 200
            assert not (await client.get('/api/health')).json()['task_running']
        finally:
            release.set()
            await pending
