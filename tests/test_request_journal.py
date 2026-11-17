"""Durable sanitized request inspection, pagination, isolation and API auth."""
import json
import httpx
import pytest

from backend.app import create_app
from backend.config import SettingsStore
from backend.request_journal import RequestJournal
from backend.storage import Database
from test_agent import FakeBridge


def options(text='Hello'):
    return {'model': 'fixture', 'messages': [{'role': 'user', 'content': text}], 'tools': [], 'max_tokens': 100}


def test_snapshot_detaches_redacts_and_tracks_changes(tmp_path):
    db = Database(tmp_path / 'requests.db')
    sid = db.create_session()['id']
    journal = RequestJournal(db, ('private-key',))
    request = options('Bearer private-key')
    first = journal.capture(sid, 'task', 1, 'a1', 'responses', 'https://example.test?key=secret', request, {})
    request['messages'][0]['content'] = 'different'
    detail = RequestJournal(db).detail(sid, first)
    assert 'private-key' not in json.dumps(detail)
    assert 'secret' not in json.dumps(detail)
    assert detail['omissions']
    assert detail['snapshot']['request']['messages'][0]['content'] != 'different'
    second = journal.capture(sid, 'task', 2, 'a2', 'responses', 'https://example.test?key=secret', request, {})
    page = journal.page(sid)
    assert page['records'][0]['changed'] == ['messages']
    assert page['records'][0]['previous_id'] == first
    assert page['records'][0]['differences']['unchanged_message_prefix'] == 0
    assert page['records'][0]['differences']['tools_added'] == []
    assert 'comparison' not in page['records'][0]
    assert journal.detail(db.create_session()['id'], second) is None
    assert journal.page(sid, before=second)['records'][0]['id'] == first


def test_retention_and_oversize_are_explicit(tmp_path, monkeypatch):
    import backend.request_journal as module
    monkeypatch.setattr(module, 'MAX_REQUESTS', 2)
    monkeypatch.setattr(module, 'MAX_REQUEST_BYTES', 200)
    db = Database(tmp_path / 'requests.db')
    sid = db.create_session()['id']
    journal = RequestJournal(db)
    ids = [journal.capture(sid, 't', 1, str(i), 'chat', '', options('x' * 1000), {}) for i in range(4)]
    assert journal.detail(sid, ids[0]) is None
    page = journal.page(sid, limit=1)
    assert page['next_before'] == ids[-1]
    assert len(journal.page(sid)['records']) == 2
    detail = journal.detail(sid, ids[-1])
    assert detail['omissions']
    assert 'messages' not in detail['snapshot'].get('request', {})
    with db.connect() as conn:
        payload = conn.execute('SELECT snapshot FROM model_requests WHERE id=?', (ids[-1],)).fetchone()['snapshot']
    assert len(payload.encode('utf-8')) <= module.MAX_REQUEST_BYTES
    db.delete_session(sid)
    assert journal.detail(sid, ids[-1]) is None


async def test_request_inspection_endpoints_require_auth_and_session_binding(tmp_path):
    db = Database(tmp_path / 'api.db')
    settings = SettingsStore(tmp_path / 'settings.json')
    bridge = FakeBridge(); bridge.detail = 'fixture'
    app = create_app(db, settings, bridge, token='local')
    sid, other = db.create_session()['id'], db.create_session()['id']
    journal = RequestJournal(db)
    id = journal.capture(sid, 't', 1, 'a', 'claude', '', options(), {})
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url='http://testserver') as api:
        endpoint = f'/api/sessions/{sid}/requests'
        assert (await api.get(endpoint)).status_code == 401
        api.headers['Authorization'] = 'Bearer local'
        assert (await api.get(endpoint)).json()['records'][0]['id'] == id
        assert (await api.get(endpoint + '/' + str(id))).json()['snapshot']['protocol'] == 'claude'
        assert (await api.get(f'/api/sessions/{other}/requests/{id}')).status_code == 404
        assert (await api.get('/api/sessions/missing/requests')).status_code == 404
