"""Blender target isolation against local protocol fixtures, not real scene acceptance."""
import json
import sys
from pathlib import Path
from types import SimpleNamespace as NS

import httpx
import pytest
from pydantic import ValidationError

from backend.agent import CadAgent
from backend.app import ChatRequest, create_app
from backend.blender import BLENDER_PACKAGE, BlenderToolCatalog, blender_preset
from backend.config import SettingsStore
from backend.execution import ExecutionControl
from backend.external_mcp import ExternalMcpHub
from backend.external_mcp_config import ServerDraft, ServerStore
from backend.knowledge import KnowledgeBase
from backend.prompting import round_instructions
from backend.storage import Database
from test_agent import FakeBridge, FakeClient, chunk
from test_engineering import tool


def test_preset_is_offline_bundled_and_never_requires_uvx(monkeypatch, tmp_path):
    monkeypatch.setattr('backend.blender.ROOT', tmp_path)
    preset = blender_preset()
    assert preset['command'].endswith('forma-blender-mcp.exe')
    assert preset['bundled'] == 'blender' and not preset['bundle_available']
    assert preset['args'] == ['--host', '127.0.0.1', '--port', '9876']
    assert 'uvx' not in preset['command']
    assert ServerDraft.model_validate({k: preset[k] for k in ('name', 'transport', 'command', 'args', 'cwd', 'url', 'enabled', 'bundled')})


def test_blender_scope_filters_other_servers_and_tracks_disconnect():
    def connection(names, running=True):
        return NS(tools=[{'name': n} for n in names], running=running)
    connections = {'blender': connection(['get_scene_info', 'execute_blender_code']),
                   'generic': connection(['fixture_echo']),
                   'dead': connection(['get_scene_info', 'execute_blender_code'], False)}
    hub = NS(connections=connections,
             tools=[{'name': 'ext_blender_scene'}, {'name': 'ext_generic_echo'}, {'name': 'ext_dead_scene'}],
             tool_name=lambda sid, name: f'ext_{sid}_{name}')
    scope = BlenderToolCatalog(hub)
    assert scope.tools == [{'name': 'ext_blender_scene'}]
    assert scope.baseline_names == {'ext_blender_get_scene_info'}
    connections['blender'].running = False
    assert not scope.running and not scope.tools
    assert not BlenderToolCatalog(None).running and not BlenderToolCatalog(None).tools


def test_backend_storage_migrates_legacy_runs_and_preserves_identity(tmp_path):
    import sqlite3
    path = tmp_path / 'legacy.db'
    with sqlite3.connect(path) as conn:
        conn.execute('CREATE TABLE runs (id TEXT PRIMARY KEY, session_id TEXT NOT NULL, owner_pid INTEGER NOT NULL, '
                     'status TEXT NOT NULL, message TEXT NOT NULL, detail TEXT NOT NULL DEFAULT "", '
                     'created_at TEXT NOT NULL, updated_at TEXT NOT NULL, finished_at TEXT)')
        conn.execute("INSERT INTO runs VALUES('old','session',0,'completed','legacy','','now','now','now')")
    db = Database(path)
    assert db.run('old')['modeling_backend'] == 'solidworks'
    sid = db.create_session()['id']
    run = db.create_run(sid, 'Blender task', modeling_backend='blender')
    assert Database(path).run(run['id'])['modeling_backend'] == 'blender'
    with pytest.raises(ValueError): db.create_run(sid, 'Invalid', modeling_backend='other')


def test_backend_field_and_instructions_have_no_solidworks_requirement():
    assert ChatRequest(session_id='s', message='x').modeling_backend == 'solidworks'
    with pytest.raises(ValidationError): ChatRequest(session_id='s', message='x', modeling_backend='unknown')
    text = round_instructions(rounds=0, calls=0, connected=True, recovery=False, tools_available=True, backend='blender')
    assert 'SELECTED MODELING BACKEND: Blender MCP' in text
    assert 'sw_status.recentOps' not in text and 'CAD executor is disconnected' not in text
    text = round_instructions(rounds=0, calls=0, connected=False, recovery=False, tools_available=True,
                              backend='blender', external_connected=True)
    assert 'No compatible Blender MCP service' in text
    assert 'do not fall back' in text


async def test_preset_api_is_authenticated_and_does_not_save_or_launch(tmp_path):
    db = Database(tmp_path / 'api.db')
    app = create_app(db, SettingsStore(tmp_path / 'settings.json'), FakeBridge(), token='fixture')
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url='http://testserver') as client:
        assert (await client.get('/api/mcp/presets/blender')).status_code == 401
        client.headers['Authorization'] = 'Bearer fixture'
        result = await client.get('/api/mcp/presets/blender')
        assert result.status_code == 200 and result.json()['package'] == BLENDER_PACKAGE
        assert (await client.get('/api/extensions')).json()['servers'] == []
        assert not app.state.extensions.connections
        sid = db.create_session()['id']
        for extra in ({'engineering_mode': True}, {'recovery_task_id': 'old'}):
            response = await client.post('/api/chat', json={'session_id': sid, 'message': 'Inspect Blender',
                'modeling_backend': 'blender', **extra})
            assert response.status_code == 400 and 'Blender' in response.json()['detail']
        assert db.runs(sid) == []


async def test_api_applies_and_resets_modeling_backend(tmp_path, monkeypatch):
    monkeypatch.setenv('FORMA_API_KEY', 'fixture')
    db = Database(tmp_path / 'target.db')
    app = create_app(db, SettingsStore(tmp_path / 'settings.json'), FakeBridge(), token='fixture')
    seen = []
    async def run(session_id, query, emit):
        seen.append(app.state.agent.modeling_backend)
        await emit({'type': 'token', 'content': 'fixture'})
    monkeypatch.setattr(app.state.agent, 'run', run)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url='http://testserver',
                                  headers={'Authorization': 'Bearer fixture'}) as client:
        sid = db.create_session()['id']
        response = await client.post('/api/chat', json={'session_id': sid, 'message': 'Inspect', 'modeling_backend': 'blender'})
        assert response.status_code == 200 and seen == ['blender']
        assert app.state.agent.modeling_backend == 'solidworks'
        assert db.runs(sid)[0]['modeling_backend'] == 'blender'
        run_id = db.runs(sid)[0]['id']
        db.update_run(run_id, 'interrupted')
        assert (await client.post(f'/api/tasks/{run_id}/recovery-check')).status_code == 409
        blocked = await client.post('/api/chat', json={'session_id': sid, 'message': 'Inspect', 'recovery_task_id': run_id})
        assert blocked.status_code == 409
        events = [json.loads(line[6:]) for line in response.text.splitlines() if line.startswith('data: ')]
        assert next(e for e in events if e['type'] == 'model_selected')['modeling_backend'] == 'blender'


@pytest.mark.parametrize('solidworks_connected', [True, False])
async def test_agent_observes_edits_and_verifies_only_blender_tools(tmp_path, monkeypatch, solidworks_connected):
    db = Database(tmp_path / 'model.db')
    servers = ServerStore(db)
    server = servers.save(ServerDraft(name='Blender fixture', command=sys.executable,
        args=[str(Path(__file__).with_name('blender_mcp_fixture.py'))]))
    hub = ExternalMcpHub(servers, tmp_path)
    await hub.connect(server['id'])
    try:
        scene = hub.tool_name(server['id'], 'get_scene_info')
        edit = hub.tool_name(server['id'], 'execute_blender_code')
        calls = [[chunk(calls=[tool(scene, {'_forma_purpose': 'Inspect the fixture scene'}, 'scene')])],
                 [chunk(calls=[tool(edit, {'code': 'fixture-only', '_forma_purpose': 'Record a synthetic cube request'}, 'edit')])],
                 [chunk(calls=[tool(scene, {'_forma_purpose': 'Check fixture dimensions'}, 'verify')])],
                 [chunk('Verified protocol fixture only; no actual Blender geometry was created.')]]
        captures, events = [], []
        monkeypatch.setenv('FORMA_API_KEY', 'fixture')
        monkeypatch.setattr('backend.agent.AsyncOpenAI', lambda **kw: FakeClient(calls, captures))
        bridge = FakeBridge(); bridge.running = solidworks_connected
        settings = SettingsStore(tmp_path / 'settings.json')
        agent = CadAgent(db, settings, bridge, KnowledgeBase(db, settings))
        agent.extensions = hub; agent.modeling_backend = 'blender'
        sid = db.create_session()['id']; run = db.create_run(sid, 'Create cube in Blender')
        async def emit(event):
            events.append(event)
            if event['type'] == 'approval_required': agent.control.decide(event['approval']['id'], True)
        agent.control = ExecutionControl(db, bridge, run['id'], emit)
        await agent.run(sid, 'Create cube in Blender', emit)
        assert bridge.calls == []
        assert len([e for e in events if e['type'] == 'approval_required']) == 3
        assert len(db.checkpoints(run['id'])) == 3
        for request in captures:
            names = {t['function']['name'] for t in request.get('tools', [])}
            assert not names & {'sw_status', 'sw_connect', 'executor_history', 'engineering_plan'}
            text = request['messages'][0]['content']
            assert 'precise Blender modeling assistant' in text and 'scale_length' in text
            assert 'CAD executor is disconnected' not in text
            assert scene in names
        assert 'FixtureCube' in json.loads(db.checkpoints(run['id'])[-1]['result']['content'])['objects'][0]['name']
    finally:
        await hub.close()


async def test_blender_target_without_service_does_not_fall_back_to_solidworks(tmp_path, monkeypatch):
    monkeypatch.setenv('FORMA_API_KEY', 'fixture')
    captures = []
    monkeypatch.setattr('backend.agent.AsyncOpenAI', lambda **kw: FakeClient([[chunk('No Blender MCP service is connected.')]], captures))
    db = Database(tmp_path / 'empty.db'); settings = SettingsStore(tmp_path / 'settings.json')
    bridge = FakeBridge(); agent = CadAgent(db, settings, bridge, KnowledgeBase(db, settings))
    agent.modeling_backend = 'blender'
    sid = db.create_session()['id']; db.create_run(sid, 'Blender scene')
    async def emit(event): pass
    await agent.run(sid, 'Blender scene', emit)
    assert bridge.calls == []
    assert {t['function']['name'] for t in captures[0]['tools']} == {'request_clarification'}
    assert 'No compatible Blender MCP service' in captures[0]['messages'][0]['content']
