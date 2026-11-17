"""Retention and paging use synthetic text/model returns, never live CAD geometry."""
import hashlib
import json
import re
from types import SimpleNamespace as NS

import httpx
import pytest

from backend import tool_results
from backend.agent import CadAgent
from backend.app import create_app
from backend.config import SettingsStore
from backend.knowledge import KnowledgeBase
from backend.storage import Database
from backend.tool_results import ToolResultStore, INLINE_CHARS, PAGE_JSON_CHARS
from test_agent import FakeBridge, FakeClient, FakeStream, chunk


@pytest.fixture
def retained(tmp_path):
    db = Database(tmp_path / 'results.db')
    return db, ToolResultStore(db), db.create_session()['id']


def test_small_results_keep_exact_text_and_do_not_enable_reader(retained):
    db, store, sid = retained
    assert store.retain(sid, 'model_info', 'one', 'small') == ('small', {})
    assert not store.available(sid)


def test_preview_preserves_both_ends_and_full_text_survives_restart(retained):
    db, store, sid = retained
    text = 'HEAD EVIDENCE\n' + 'middle ' * 4000 + '\nTAIL FAILURE DETAIL'
    preview, meta = store.retain(sid, 'ext_blender_scene', 'call-a', text)
    assert len(preview) == INLINE_CHARS
    assert preview.startswith('HEAD EVIDENCE') and preview.endswith('TAIL FAILURE DETAIL')
    assert 'tool_result_read' in preview and meta['result_id'] in preview
    assert meta['sha256'] == hashlib.sha256(text.encode()).hexdigest()
    store = ToolResultStore(Database(db.path))
    joined, offset = '', 0
    while offset is not None:
        page = store.read(sid, meta['result_id'], offset, 8000)
        assert len(json.dumps(page, ensure_ascii=False)) <= PAGE_JSON_CHARS
        joined += page['text']; offset = page['next_offset']
    assert joined == text
    assert page['historical'] and page['tool_name'] == 'ext_blender_scene'
    assert store.read(sid, meta['result_id'], len(text))['text'] == ''


@pytest.mark.parametrize('character', ['\n', '\x00', '\U0001f600', '\u4e2d'])
def test_pages_bound_actual_json_and_preserve_unicode(retained, character):
    db, store, sid = retained
    text = character * 25000
    _, meta = store.retain(sid, 'fixture', 'call', text)
    offset, parts = 0, []
    while offset is not None:
        page = store.read(sid, meta['result_id'], offset, 8000)
        assert len(json.dumps(page, ensure_ascii=False)) <= PAGE_JSON_CHARS
        assert page['total_chars'] == len(text)
        assert page['next_offset'] is None or page['next_offset'] > offset
        parts.append(page['text']); offset = page['next_offset']
    assert ''.join(parts) == text


def test_session_scope_and_cascade_cleanup(retained):
    db, store, sid = retained
    _, meta = store.retain(sid, 'model_info', 'call', 'x' * 19000)
    other = db.create_session()['id']
    with pytest.raises(ValueError, match='missing or expired'): store.read(other, meta['result_id'])
    db.delete_session(sid)
    assert not store.available(sid)
    with pytest.raises(ValueError, match='missing or expired'): store.read(sid, meta['result_id'])


@pytest.mark.parametrize('changes', [dict(result_id='../file'), dict(offset=-1), dict(offset=True),
                                     dict(limit=0), dict(limit=8001), dict(limit=True), dict(offset=999999)])
def test_invalid_windows_never_read_another_source(retained, changes):
    db, store, sid = retained
    _, meta = store.retain(sid, 'fixture', 'call', 'x' * 19000)
    with pytest.raises(ValueError): store.read(sid, **{'result_id': meta['result_id'], **changes})


def test_retention_budgets_evict_old_results_and_keep_newest(retained, monkeypatch):
    db, store, sid = retained
    monkeypatch.setattr(tool_results, 'SESSION_BYTES', 40000)
    monkeypatch.setattr(tool_results, 'GLOBAL_BYTES', 60000)
    ids = [store.retain(sid, 'fixture', str(i), str(i) * 19000)[1]['result_id'] for i in range(3)]
    with pytest.raises(ValueError, match='expired'): store.read(sid, ids[0])
    assert store.read(sid, ids[-1])['text'] == '2' * 4000
    other = db.create_session()['id']
    for i in range(2): store.retain(other, 'fixture', str(i), 'z' * 19000)
    with db.connect() as conn:
        assert conn.execute('SELECT SUM(bytes) FROM retained_tool_results').fetchone()[0] <= 60000
    assert store.read(sid, ids[-1])['text'] == '2' * 4000


def test_capacity_failure_does_not_publish_a_fake_recovery_id(retained, monkeypatch):
    db, store, sid = retained
    monkeypatch.setattr(tool_results, 'MAX_RESULT_BYTES', 10)
    with pytest.raises(ValueError, match='capacity'): store.retain(sid, 'fixture', 'call', 'x' * 19000)
    assert not store.available(sid)


def test_corrupted_retained_text_fails_integrity_check(retained):
    db, store, sid = retained
    _, meta = store.retain(sid, 'fixture', 'call', 'x' * 19000)
    with db.connect() as conn:
        conn.execute('UPDATE retained_tool_results SET content=? WHERE id=?', ('tampered', meta['result_id']))
    with pytest.raises(ValueError, match='integrity'): store.read(sid, meta['result_id'])


def test_budget_failure_rolls_back_insert_and_eviction(retained, monkeypatch):
    db, store, sid = retained
    _, first = store.retain(sid, 'fixture', 'first', 'x' * 19000)
    monkeypatch.setattr(tool_results, 'SESSION_BYTES', 10000)
    with pytest.raises(ValueError, match='budget'): store.retain(sid, 'fixture', 'second', 'y' * 19000)
    assert store.read(sid, first['result_id'])['text'] == 'x' * 4000
    with db.connect() as conn:
        assert conn.execute('SELECT COUNT(*) FROM retained_tool_results').fetchone()[0] == 1


async def test_retained_result_api_auth_scope_and_window_validation(tmp_path):
    db = Database(tmp_path / 'api.db')
    app = create_app(db, SettingsStore(tmp_path / 'settings.json'), FakeBridge(), token='fixture')
    sid, other = db.create_session()['id'], db.create_session()['id']
    _, meta = ToolResultStore(db).retain(sid, 'fixture', 'call', 'x' * 19000)
    path = f'/api/sessions/{sid}/tool-results/{meta["result_id"]}'
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url='http://testserver') as client:
        assert (await client.get(path)).status_code == 401
        client.headers['Authorization'] = 'Bearer fixture'
        response = await client.get(path + '?offset=9000&limit=100')
        assert response.status_code == 200 and response.json()['text'] == 'x' * 100
        assert (await client.get(path.replace(sid, other))).status_code == 404
        for query in ('offset=-1', 'limit=8001', 'limit=0'):
            assert (await client.get(path + '?' + query)).status_code == 422
        assert (await client.get(path.replace(meta['result_id'], 'bad'))).status_code == 404


@pytest.mark.parametrize('storage_failure', [False, True])
async def test_agent_reads_retained_tool_text_without_reexecuting_cad(tmp_path, monkeypatch, storage_failure):
    db = Database(tmp_path / 'agent.db'); settings = SettingsStore(tmp_path / 'settings.json')
    monkeypatch.setenv('FORMA_API_KEY', 'fixture')
    bridge = FakeBridge()
    bridge.tools = [*bridge.tools, {'name': 'model_info', 'description': 'Read model information',
        'inputSchema': {'type': 'object', 'properties': {}, 'additionalProperties': False}}]
    original = 'HEAD\n' + 'middle-value ' * 2500 + '\nTAIL IMPORTANT FAILURE'
    async def execute(name, args):
        bridge.calls.append((name, args))
        return {'content': original if name == 'model_info' else '{}', 'is_error': name == 'model_info'}
    bridge.call = execute
    captures, events = [], []
    class Client(FakeClient):
        async def create(self, **kwargs):
            captures.append(kwargs)
            number = len(captures)
            if number == 1:
                call = NS(index=0, id='inspect', function=NS(name='model_info', arguments='{}'))
            elif number == 2 and not storage_failure:
                last = kwargs['messages'][-1]['content']
                result_id = re.search(r'result_id=([a-f0-9]{32})', last)[1]
                call = NS(index=0, id='read-retained', function=NS(name='tool_result_read',
                    arguments=json.dumps({'result_id': result_id, 'offset': 12000, 'limit': 500})))
            else:
                return FakeStream([chunk('Synthetic evidence checked; no live CAD claim.')])
            return FakeStream([chunk(calls=[call])])
    monkeypatch.setattr('backend.agent.AsyncOpenAI', lambda **kw: Client([], captures))
    agent = CadAgent(db, settings, bridge, KnowledgeBase(db, settings))
    if storage_failure:
        def fail(*args): raise OSError('fixture storage failure')
        monkeypatch.setattr(agent.result_store, 'retain', fail)
    sid = db.create_session()['id']; db.add_message(sid, 'user', 'Read model_info then inspect returned evidence')
    async def emit(event): events.append(event)
    await agent.run(sid, 'Read model_info then inspect returned evidence', emit)
    assert [name for name, args in bridge.calls].count('model_info') == 1
    assert not any(name == 'tool_result_read' for name, args in bridge.calls)
    observed = next(e for e in events if e['type'] == 'tool_result' and e['name'] == 'model_info')
    assert observed['is_error'] is True
    if storage_failure:
        assert observed['content'] == original and 'result_id' not in observed
        assert any('留存失败' in e.get('message', '') for e in events)
    else:
        assert observed['result_id'] and observed['content'].endswith('TAIL IMPORTANT FAILURE')
        assert 'tool_result_read' not in {t['function']['name'] for t in captures[0]['tools']}
        assert 'tool_result_read' in {t['function']['name'] for t in captures[1]['tools']}
        read = next(e for e in events if e['type'] == 'tool_result' and e['name'] == 'tool_result_read')
        assert json.loads(read['content'])['text'] == original[12000:12500]
        assert not read['is_error']
