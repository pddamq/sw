import asyncio
import os
import sqlite3

import httpx
import pytest

from backend.app import create_app
from backend.config import SettingsStore
from backend.knowledge import KnowledgeBase
from backend.storage import Database


@pytest.fixture
def environment(tmp_path, monkeypatch):
    monkeypatch.setenv('FORMA_API_KEY', 'fixture-key')
    db = Database(tmp_path / 'runs.db')
    config = SettingsStore(tmp_path / 'settings.json')
    app = create_app(db, config, token='fixture')
    return db, config, app


def client(app):
    return httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url='http://testserver',
                            headers={'Authorization': 'Bearer fixture'})


async def test_run_persists_and_export_groups_events(environment, monkeypatch):
    db, config, app = environment
    async def fake(session_id, query, emit):
        await emit({'type': 'tool_result', 'name': 'fixture', 'content': 'no CAD operation', 'is_error': False})
        db.add_message(session_id, 'assistant', 'fixture')
    monkeypatch.setattr(app.state.agent, 'run', fake)
    async with client(app) as api:
        session = (await api.post('/api/sessions', json={})).json()
        for _ in range(2):
            response = await api.post('/api/chat', json={'session_id': session['id'], 'message': 'test'})
            assert response.status_code == 200
        runs = (await api.get(f"/api/sessions/{session['id']}/tasks")).json()
        assert len(runs) == 2 and all(r['status'] == 'completed' for r in runs)
        events = (await api.get(f"/api/sessions/{session['id']}/events", params={'task_id': runs[0]['id']})).json()
        assert all(e['event']['task_id'] == runs[0]['id'] for e in events)
        record = (await api.get('/api/tasks/' + runs[0]['id'])).json()
        assert record['finished_at']
        exported = await api.get(f"/api/sessions/{session['id']}/export")
        assert exported.status_code == 200
        assert len(exported.json()['tasks']) == 2
        assert len(exported.json()['messages']) == 4
        assert 'attachment' in exported.headers['content-disposition']
        assert (await api.get('/api/health')).json()['active_task'] is None
        await api.delete(f"/api/sessions/{session['id']}")
        assert db.run(runs[0]['id']) is None


async def test_cancel_is_idempotent_while_operation_drains(environment, monkeypatch):
    db, config, app = environment
    started, draining, release = asyncio.Event(), asyncio.Event(), asyncio.Event()
    cancellations = []
    async def fake(session_id, query, emit):
        started.set()
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            cancellations.append('first')
            draining.set()
            try:
                await release.wait()
            except asyncio.CancelledError:
                cancellations.append('duplicate')
            raise
    monkeypatch.setattr(app.state.agent, 'run', fake)
    async with client(app) as api:
        session = (await api.post('/api/sessions', json={})).json()
        pending = asyncio.create_task(api.post('/api/chat', json={'session_id': session['id'], 'message': 'test'}))
        await asyncio.wait_for(started.wait(), 2)
        try:
            assert (await api.post('/api/tasks/cancel')).json()['requested'] is True
            await asyncio.wait_for(draining.wait(), 2)
            assert (await api.post('/api/tasks/cancel')).json()['requested'] is False
            health = (await api.get('/api/health')).json()
            assert health['task_running'] and health['active_task']['status'] == 'cancelling'
            assert (await api.put('/api/settings', json={'model': 'other'})).status_code == 409
        finally:
            release.set()
            await pending
        assert cancellations == ['first']
        assert db.runs(session['id'])[0]['status'] == 'cancelled'
        assert (await api.get('/api/health')).json()['task_running'] is False


async def test_failed_run_has_redacted_detail(environment, monkeypatch):
    db, config, app = environment
    async def fail(*args):
        raise RuntimeError('fixture-key provider error')
    monkeypatch.setattr(app.state.agent, 'run', fail)
    async with client(app) as api:
        session = (await api.post('/api/sessions', json={})).json()
        await api.post('/api/chat', json={'session_id': session['id'], 'message': 'test'})
        run = db.runs(session['id'])[0]
        assert run['status'] == 'failed'
        assert 'fixture-key' not in run['detail'] and '[redacted]' in run['detail']


def test_recover_only_runs_owned_by_dead_process(environment, monkeypatch):
    db, _, _ = environment
    session = db.create_session()
    dead = db.create_run(session['id'], 'dead')
    live = db.create_run(session['id'], 'live')
    with db.connect() as conn:
        conn.execute('UPDATE runs SET owner_pid=-1 WHERE id=?', (dead['id'],))
    import backend.runtime as runtime
    monkeypatch.setattr(runtime, 'process_alive', lambda pid: pid == os.getpid())
    db.recover_interrupted_runs()
    assert db.run(dead['id'])['status'] == 'interrupted'
    assert db.run(live['id'])['status'] == 'running'
    assert db.messages(session['id'])[0]['content'] == 'dead'


def test_upgrade_old_event_schema(tmp_path):
    path = tmp_path / 'legacy.db'
    with sqlite3.connect(path) as conn:
        conn.execute('CREATE TABLE events (id INTEGER PRIMARY KEY,session_id TEXT,payload TEXT,created_at TEXT)')
    db = Database(path)
    session = db.create_session()
    db.event(session['id'], {'type': 'status', 'message': 'old-compatible'})
    assert db.events(session['id'])[0]['event']['message'] == 'old-compatible'


async def test_embedding_outage_preserves_lexical_results(environment, monkeypatch):
    db, config, app = environment
    config.update({'embedding_model': 'fixture-vector'})
    kb = KnowledgeBase(db, config)
    db.add_document('extrude.md', ['底板拉伸厚度使用毫米'], [[1.0, 0.0]], kb.embedding_identity())
    async def fail(texts):
        raise RuntimeError('provider unavailable')
    monkeypatch.setattr(kb, 'embed', fail)
    report = {}
    sources = await kb.search('底板拉伸', report=report)
    assert sources[0]['name'] == 'extrude.md'
    assert report['mode'] == 'bm25' and report['warning']


async def test_knowledge_preview_and_offline_status(environment):
    db, config, app = environment
    db.add_document('plate.md', ['plate extrude depth mm'], None, '')
    async with client(app) as api:
        result = (await api.post('/api/knowledge/preview', json={'query': 'extrude'})).json()
        assert result['mode'] == 'bm25' and result['sources'][0]['name'] == 'plate.md'
        assert (await api.post('/api/knowledge/preview', json={'query': ' '})).status_code == 422
        assert (await api.post('/api/solidworks/status')).status_code == 409
