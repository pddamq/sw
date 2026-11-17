import asyncio
import json
from pathlib import Path
import sys
from types import SimpleNamespace as NS

import httpx
import pytest

from backend.agent import CadAgent
from backend.app import create_app
from backend.config import Settings, SettingsStore
from backend.knowledge import KnowledgeBase
from backend.mcp_bridge import McpBridge, SHUTDOWN_GRACE_SECONDS, call_timeout
from backend.setup import webview_version
from backend.storage import Database
from test_agent import FakeBridge, FakeClient, chunk


@pytest.mark.parametrize('value,expected', [('0.0.0.0', None), ('', None), ('invalid', None), (None, None),
    ('85.0.1.0', None), ('142.0.3595.65', (142, 0, 3595, 65))])
def test_webview_registry_tombstones_are_not_installed_runtimes(value, expected):
    assert webview_version(value) == expected


def test_timeouts_cover_real_upstream_workflows():
    assert call_timeout('sim_static', {}).total_seconds() >= 1800
    assert call_timeout('sim_static', {'convergence': True}).total_seconds() >= 3 * 1800
    assert call_timeout('sim_run', {}).total_seconds() >= 1800
    for name in ('feature_recognize', 'hardware_part', 'config_table', 'sim_plot', 'vba_run'):
        assert call_timeout(name, {}).total_seconds() >= 600
    assert SHUTDOWN_GRACE_SECONDS > call_timeout('sim_static', {'convergence': True}).total_seconds()


async def test_worker_cancellation_resolves_active_call():
    bridge = McpBridge()
    pending = None
    try:
        await bridge.connect(Settings(mcp_command=sys.executable,
            mcp_args=[str(Path(__file__).with_name('mcp_fixture.py'))]))
        pending = asyncio.create_task(bridge.call('fixture_wait', {'seconds': 20}))
        await asyncio.sleep(0.15)
        bridge.task.cancel()
        await asyncio.wait_for(asyncio.shield(bridge.task), 3)
        completed, _ = await asyncio.wait({pending}, timeout=1)
        assert pending in completed, 'Worker cancellation must resolve the active future before clearing it'
        with pytest.raises(RuntimeError, match='interrupted'):
            await pending
    finally:
        # Avoid hanging the test loop if this regression is reintroduced.
        if pending is not None and not pending.done():
            frame = pending.get_coro().cr_frame
            future = frame.f_locals.get('future') if frame else None
            if future is not None and not future.done():
                future.set_exception(RuntimeError('regression test cleanup'))
            await asyncio.gather(pending, return_exceptions=True)
        await bridge.disconnect()


@pytest.mark.parametrize('reason', ['length', 'content_filter', None])
async def test_incomplete_model_output_never_executes_partial_tool_calls(tmp_path, monkeypatch, reason):
    import backend.agent as module
    import backend.model_retry as retry_module
    async def skip_wait(delay):
        pass
    monkeypatch.setattr(retry_module, 'wait_before_retry', skip_wait)
    monkeypatch.setenv('FORMA_API_KEY', 'fixture')
    config = SettingsStore(tmp_path / 'settings.json')
    db = Database(tmp_path / 'app.db')
    session = db.create_session()
    db.add_message(session['id'], 'user', '检查连接')
    call = NS(index=0, id='partial', function=NS(name='sw_status', arguments='{}'))
    response = chunk('尚未完整生成', [call])
    response.choices[0].finish_reason = reason
    captures, events = [], []
    class BrokenStream:
        async def __aenter__(self):
            return self
        async def __aexit__(self, *args):
            return False
        def __aiter__(self):
            return self.generate()
        async def generate(self):
            yield response
    class BrokenClient(FakeClient):
        async def create(self, **kwargs):
            captures.append(kwargs)
            return BrokenStream()
    monkeypatch.setattr(module, 'AsyncOpenAI', lambda **kwargs: BrokenClient([], captures))
    bridge = FakeBridge()
    async def emit(event):
        events.append(event)
    with pytest.raises(RuntimeError, match='5 次' if reason is None else '未完整生成'):
        await CadAgent(db, config, bridge, KnowledgeBase(db, config)).run(session['id'], '检查连接', emit)
    assert len(captures) == (6 if reason is None else 1)
    assert bridge.calls == [('sw_status', {})], 'Only the preflight read should have run'
    assert len(db.messages(session['id'])) == 1
    assert not any(item['type'] in {'token', 'answer'} for item in events)
    assert not any(item['type'] == 'tool_start' and item.get('name') == 'partial' for item in events)


@pytest.mark.parametrize('duplicate', [False, True])
async def test_missing_or_duplicate_model_tool_ids_are_never_executed(tmp_path, monkeypatch, duplicate):
    import backend.agent as module
    monkeypatch.setenv('FORMA_API_KEY', 'fixture')
    db = Database(tmp_path / 'db')
    config = SettingsStore(tmp_path / 'settings.json')
    session = db.create_session()
    db.add_message(session['id'], 'user', 'check')
    ids = ['same', 'same'] if duplicate else ['']
    calls = [NS(index=index, id=id_, function=NS(name='sw_status', arguments='{}')) for index, id_ in enumerate(ids)]
    monkeypatch.setattr(module, 'AsyncOpenAI', lambda **kwargs: FakeClient([[chunk(calls=calls)]] * 3, []))
    bridge = FakeBridge()
    async def emit(event):
        pass
    with pytest.raises(RuntimeError, match='协议校验'):
        await CadAgent(db, config, bridge, KnowledgeBase(db, config)).run(session['id'], 'check', emit)
    assert bridge.calls == [('sw_status', {})]


async def test_discovery_does_not_invalidate_other_advertised_calls_in_same_batch(tmp_path, monkeypatch):
    import backend.agent as module
    monkeypatch.setenv('FORMA_API_KEY', 'fixture')
    config = SettingsStore(tmp_path / 'settings.json')
    db = Database(tmp_path / 'app.db')
    session = db.create_session()
    db.add_message(session['id'], 'user', 'hole')
    bridge = FakeBridge()
    bridge.tools = [*bridge.tools, {'name': 'feature_hole', 'description': 'hole',
                                  'inputSchema': {'type': 'object', 'properties': {}, 'additionalProperties': False}}]
    # Fill the bounded discovery window so the old tool really is evicted next turn.
    bridge.tools += [{'name': f'new_tool_{i}', 'description': 'replacement',
                      'inputSchema': {'type': 'object', 'properties': {}, 'additionalProperties': False}}
                     for i in range(14)]
    calls = [NS(index=0, id='discovery', function=NS(name='discover_tools', arguments='{"query":"replacement"}')),
             NS(index=1, id='hole', function=NS(name='feature_hole', arguments='{}'))]
    captures, events = [], []
    monkeypatch.setattr(module, 'AsyncOpenAI', lambda **kwargs: FakeClient([[chunk(calls=calls)], [chunk('fixture done')]], captures))
    async def emit(event):
        events.append(event)
    await CadAgent(db, config, bridge, KnowledgeBase(db, config)).run(session['id'], 'hole', emit)
    assert ('feature_hole', {}) in bridge.calls
    assert not any(item.get('name') == 'feature_hole' and item.get('is_error') for item in events)
    assert 'feature_hole' not in {t['function']['name'] for t in captures[1]['tools']}


async def test_refresh_attaches_sw_started_after_initial_connection(tmp_path):
    class AppearingSw:
        running = True
        tools = [{'name': 'sw_status'}, {'name': 'sw_connect'}]
        sw_status = {'attached': False}
        configuration = None
        detail = 'fixture, no real CAD'
        def __init__(self):
            self.calls = []
        async def call(self, name, arguments):
            self.calls.append((name, arguments))
            if name == 'sw_connect':
                self.sw_status = {'attached': True, 'revision': 'fixture-only'}
            return {'data': dict(self.sw_status), 'content': json.dumps(self.sw_status), 'is_error': False}
    bridge = AppearingSw()
    app = create_app(Database(tmp_path / 'db'), SettingsStore(tmp_path / 'settings.json'), bridge, token='test')
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url='http://testserver',
                                 headers={'Authorization': 'Bearer test'}) as api:
        result = await api.post('/api/solidworks/status')
        assert result.status_code == 200 and result.json()['data']['attached'] is True
        assert bridge.calls == [('sw_status', {}), ('sw_connect', {'launch': False}), ('sw_status', {})]
        assert (await api.get('/api/health')).json()['solidworks']['connected']


def test_missing_old_bundled_path_is_relocated_without_touching_custom_commands(tmp_path, monkeypatch):
    import backend.config as module
    root = tmp_path / 'new-location' / '_internal'
    bundled = root / 'resources' / 'mcp' / 'solidworks-mcp.exe'
    bundled.parent.mkdir(parents=True)
    bundled.write_bytes(b'fixture-not-executable')
    monkeypatch.setattr(module, 'ROOT', root)
    old = tmp_path / 'old-location' / '_internal' / 'resources' / 'mcp' / 'solidworks-mcp.exe'
    path = tmp_path / 'settings.json'
    path.write_text(json.dumps({'mcp_command': str(old), 'mcp_cwd': str(old.parent), 'model': 'kept'}), 'utf-8')
    store = SettingsStore(path)
    assert store.value.mcp_command == str(bundled) and store.value.mcp_cwd == str(bundled.parent)
    assert store.value.model == 'kept' and store.public()['configuration_warnings']
    assert json.loads(path.read_text('utf-8'))['mcp_command'] == str(old), 'Read-only startup must preserve original config'
    for command, args in [(str(tmp_path / 'custom' / 'solidworks-mcp.exe'), []), (str(old), ['--custom'])]:
        path.write_text(json.dumps({'mcp_command': command, 'mcp_args': args}), 'utf-8')
        assert SettingsStore(path).value.mcp_command == command
