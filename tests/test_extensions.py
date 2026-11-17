"""Skills and external MCP use isolated stores and synthetic/local protocol fixtures only."""
import asyncio
import copy
import json
import socket
import sys
from pathlib import Path
from types import SimpleNamespace as NS

import httpx
import pytest
from pydantic import ValidationError

from backend.agent import CadAgent
from backend.app import create_app
from backend.config import SettingsStore
from backend.execution import DecisionStopped, ExecutionControl, ExecutionHalted
from backend.external_mcp import ExternalMcpHub, ToolCatalog
from backend.external_mcp_config import ServerDraft, ServerStore
from backend.knowledge import KnowledgeBase
from backend.skills import SkillStore, read_skill
from backend.storage import Database
from test_agent import FakeBridge, FakeClient, chunk
from test_engineering import tool

MANIFEST = b'---\nname: case-review\ndescription: Review case requirements and compare interfaces.\n---\nRead reference.md before reviewing.\n'


def stores(tmp_path):
    db = Database(tmp_path / 'extensions.db')
    return db, SkillStore(db), ServerStore(db)


def test_skill_bundle_paths_snapshot_disable_and_delete(tmp_path):
    db, skills, _ = stores(tmp_path)
    item = skills.import_bundle([('case/SKILL.md', MANIFEST), ('case/docs/reference.md', b'Confirmed project guidance')])
    assert item['files'] == ['SKILL.md', 'docs/reference.md']
    snapshot = skills.snapshot('Review case')
    assert read_skill(snapshot, {'skill_id': item['id']})['content'].endswith('reviewing.\n')
    assert read_skill(snapshot, {'skill_id': item['id'], 'path': 'docs/reference.md'})['content'] == 'Confirmed project guidance'
    skills.set_enabled(item['id'], False)
    assert not skills.snapshot('Review case')
    assert read_skill(snapshot, {'skill_id': item['id']})['name'] == item['name']
    updated = skills.import_bundle([('SKILL.md', MANIFEST + b'Updated')])
    assert updated['id'] == item['id'] and not updated['enabled']
    skills.delete(item['id'])
    assert not SkillStore(db).list()


@pytest.mark.parametrize('files', [
    [('../SKILL.md', MANIFEST)], [('C:/SKILL.md', MANIFEST)], [('a/SKILL.md', MANIFEST), ('b/x.md', b'x')],
    [('a/SKILL.md', MANIFEST), ('b/SKILL.md', MANIFEST)], [('SKILL.md', b'no header')],
    [('SKILL.md', MANIFEST), ('x', b'\x00binary')], [('SKILL.md', MANIFEST), ('x', b'\xff')],
    [('SKILL.md', MANIFEST), ('A.md', b'x'), ('a.md', b'y')],
    [('SKILL.md', MANIFEST + b'x' * (256 * 1024))],
])
def test_invalid_skill_import_is_atomic(tmp_path, files):
    _, skills, _ = stores(tmp_path)
    with pytest.raises(ValueError): skills.import_bundle(files)
    assert skills.list() == []


def test_manual_skill_requires_slash_invocation_and_text_is_paged(tmp_path):
    _, skills, _ = stores(tmp_path)
    manifest = MANIFEST.replace(b'---\nRead', b'disable-model-invocation: true\n---\nRead') + b'x' * 9000
    item = skills.import_bundle([('SKILL.md', manifest)])
    assert skills.snapshot('case-review') == {}
    snapshot = skills.snapshot('/case-review compare interfaces')
    result = read_skill(snapshot, {'skill_id': item['id']})
    assert len(result['content']) == 8000 and result['next_offset'] == 8000
    assert read_skill(snapshot, {'skill_id': item['id'], 'offset': 8000})['next_offset'] is None
    with pytest.raises(ValueError): read_skill(snapshot, {'skill_id': item['id'], 'path': '../settings.json'})
    with pytest.raises(ValueError): read_skill({}, {'skill_id': item['id']})


@pytest.mark.parametrize('fields', [
    {'transport': 'stdio', 'command': ''}, {'transport': 'stdio', 'command': 'python', 'args': [1]},
    {'transport': 'streamable_http', 'url': 'https://user:pass@example.com/mcp'},
    {'transport': 'streamable_http', 'url': 'file:///local'},
    {'transport': 'streamable_http', 'url': 'https://example.com/mcp?key=secret'},
    {'transport': 'sse', 'url': 'http://localhost/sse', 'headers': {'Authorization': 'bad\r\nInjected: value'}},
    {'transport': 'stdio', 'command': 'python', 'env': {'BAD=NAME': 'x'}},
])
def test_invalid_mcp_configuration(fields):
    with pytest.raises((ValueError, ValidationError)): ServerDraft(name='fixture', **fields)


def test_mcp_credentials_encrypted_preserved_cleared_and_target_bound(tmp_path):
    db, _, servers = stores(tmp_path)
    item = servers.save(ServerDraft(name='fixture', command='python', env={'TOKEN': 'fixture-secret-239'}))
    assert item['env_keys'] == ['TOKEN'] and 'env' not in item
    with db.connect() as conn:
        row = dict(conn.execute('SELECT * FROM external_mcp_servers').fetchone())
    assert 'fixture-secret-239' not in json.dumps(row)
    public = servers.save(ServerDraft(id=item['id'], name='renamed service', command='python'))
    assert public['has_secrets']
    assert servers.get(item['id'], private=True)['env']['TOKEN'] == 'fixture-secret-239'
    with pytest.raises(ValueError, match='旧凭据'):
        servers.save(ServerDraft(id=item['id'], name='fixture', command='different'))
    result = servers.save(ServerDraft(id=item['id'], name='fixture', command='different', clear_secrets=True))
    assert not result['has_secrets']


def test_standard_mcp_config_import_atomic_and_no_launch(tmp_path):
    _, _, servers = stores(tmp_path)
    result = servers.import_config({'mcpServers': {
        'echo': {'command': 'definitely-not-a-program', 'args': ['--fixture']},
        'remote': {'type': 'http', 'url': 'https://example.com/mcp', 'disabled': True},
    }})
    assert len(result['servers']) == 2
    assert result['servers'][1]['transport'] == 'streamable_http' and not result['servers'][1]['enabled']
    with pytest.raises((ValueError, ValidationError)):
        servers.import_config({'mcpServers': {'ok': {'command': 'python'}, 'bad': {'unknown': 'x'}}})
    assert len(servers.list()) == 2


async def connected_hub(tmp_path):
    db, _, servers = stores(tmp_path)
    item = servers.save(ServerDraft(name='Local fixture', command=sys.executable,
        args=[str(Path(__file__).with_name('mcp_fixture.py'))]))
    hub = ExternalMcpHub(servers, tmp_path)
    await hub.connect(item['id'])
    return db, item, hub


async def test_namespaced_external_protocol_is_separate_from_cad(tmp_path):
    _, item, hub = await connected_hub(tmp_path)
    try:
        assert hub.running and len(hub.tools) == 3
        name = hub.tool_name(item['id'], 'fixture_echo')
        assert name.startswith('ext_') and len(name) <= 64
        assert (await hub.call(name, {'value': 'hello external'}))['content'] == 'hello external'
        cad = FakeBridge()
        catalog = ToolCatalog(cad, hub)
        assert len(catalog.tools) == len(cad.tools) + 3
        assert next(tool for tool in catalog.tools if tool['name'] == 'sw_status') in cad.tools
        assert all(tool['name'] != 'sw_status' for tool in hub.tools)
        assert hub.public()[0]['running']
        await hub.disconnect(item['id'])
        assert not hub.tools
    finally: await hub.close()


@pytest.mark.parametrize('decision', ['approve', 'deny', 'reconnect'])
async def test_external_calls_require_individual_approval_without_cad(tmp_path, decision):
    db, item, hub = await connected_hub(tmp_path)
    try:
        sid = db.create_session()['id']
        task = db.create_run(sid, 'External fixture')
        bridge = FakeBridge(); bridge.running = False
        control = None
        events = []
        name = hub.tool_name(item['id'], 'fixture_echo')
        async def emit(event):
            events.append(event)
            if event['type'] == 'approval_required':
                if decision == 'reconnect': hub.connections[item['id']].generation = 'new-connection'
                control.decide(event['approval']['id'], decision != 'deny')
        control = ExecutionControl(db, bridge, task['id'], emit)
        control.extensions = hub
        if decision == 'approve':
            result = await control.invoke(name, {'value': 'approved'}, purpose='Read synthetic echo')
            assert result['content'] == 'approved'
            assert db.checkpoints(task['id'])[-1]['snapshot']['external_mcp_tool'] == name
        else:
            with pytest.raises(DecisionStopped): await control.invoke(name, {'value': 'held'}, purpose='Echo')
            assert db.checkpoints(task['id']) == []
        assert any(e['type'] == 'approval_required' for e in events)
        assert bridge.calls == []
        control.read_only = True
        with pytest.raises(DecisionStopped): await control.invoke(name, {'value': 'held'})
        control.read_only = False
        control.engineering = object()
        with pytest.raises(DecisionStopped): await control.invoke(name, {'value': 'held'})
    finally: await hub.close()


async def test_external_cancellation_drains_result_and_does_not_replay(tmp_path):
    _, item, hub = await connected_hub(tmp_path)
    try:
        name = hub.tool_name(item['id'], 'fixture_wait')
        task = asyncio.create_task(hub.call(name, {'seconds': 0.3}))
        await asyncio.sleep(0.08); task.cancel()
        await asyncio.sleep(0.04)
        assert not task.done()
        await asyncio.gather(task, return_exceptions=True)
        assert (await hub.call(hub.tool_name(item['id'], 'fixture_echo'), {'value': 'after'}))['content'] == 'after'
    finally: await hub.close()


async def test_agent_reads_skill_and_discovers_external_tool_without_solidworks(tmp_path, monkeypatch):
    db, item, hub = await connected_hub(tmp_path)
    try:
        skills = SkillStore(db)
        skill = skills.import_bundle([('SKILL.md', MANIFEST)])
        sid = db.create_session()['id']
        task = db.create_run(sid, 'Run fixture')
        bridge = FakeBridge(); bridge.running = False
        external_name = hub.tool_name(item['id'], 'fixture_echo')
        calls = [
            [chunk(calls=[tool('skill_read', {'skill_id': skill['id']}, 'skill')])],
            [chunk(calls=[tool('discover_tools', {'query': 'fixture_echo'}, 'discover')])],
            [chunk(calls=[tool(external_name, {'value': 'fixture agent result', '_forma_purpose': 'Run synthetic echo'}, 'external')])],
            [chunk('Complete fixture.')],
        ]
        captures, events = [], []
        monkeypatch.setenv('FORMA_API_KEY', 'fixture')
        monkeypatch.setattr('backend.agent.AsyncOpenAI', lambda **kw: FakeClient(calls, captures))
        settings = SettingsStore(tmp_path / 'settings.json')
        agent = CadAgent(db, settings, bridge, KnowledgeBase(db, settings))
        agent.skills, agent.extensions = skills, hub
        async def emit(event):
            events.append(event)
            if event['type'] == 'approval_required':
                agent.control.decide(event['approval']['id'], True)
        agent.control = ExecutionControl(db, bridge, task['id'], emit)
        try:
            await agent.run(sid, 'Review case using fixture_echo', emit)
        except RuntimeError as exc:
            pytest.fail(str(exc) + '\n' + json.dumps([event for event in events if event['type'] in {'status', 'tool_result'}], ensure_ascii=True))
        assert any(event['type'] == 'skill_loaded' for event in events)
        assert any(event['type'] == 'approval_required' for event in events)
        assert bridge.calls == []
        assert 'AVAILABLE USER SKILLS' in captures[0]['messages'][0]['content']
        assert external_name in {tool['function']['name'] for tool in captures[2]['tools']}
        assert db.checkpoints(task['id'])[-1]['name'] == external_name
    finally: await hub.close()


async def test_extension_api_auth_import_and_save_do_not_connect(tmp_path):
    db, _, _ = stores(tmp_path)
    settings = SettingsStore(tmp_path / 'settings.json')
    bridge = FakeBridge(); bridge.running = False
    app = create_app(db, settings, bridge, token='fixture-token')
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url='http://testserver') as client:
        assert (await client.get('/api/extensions')).status_code == 401
        client.headers['Authorization'] = 'Bearer fixture-token'
        response = await client.post('/api/skills/import', files=[('files', ('SKILL.md', MANIFEST, 'text/markdown'))],
                                     data={'paths': '["case/SKILL.md"]'})
        assert response.status_code == 200, response.text
        skill_id = response.json()['id']
        assert (await client.get('/api/skills/' + skill_id)).json()['content'].startswith('---')
        response = await client.post('/api/mcp/servers', json={'name': 'saved only', 'command': 'not-a-real-executable',
            'env': {'SECRET': 'secret-api-fixture'}})
        assert response.status_code == 200, response.text
        assert 'secret-api-fixture' not in response.text
        state = (await client.get('/api/extensions')).json()
        assert len(state['skills']) == len(state['servers']) == 1
        assert not state['servers'][0]['running'] and not app.state.extensions.connections
        invalid = await client.post('/api/mcp/servers', json={'name': 'invalid', 'command': 'x', 'headers': {'Bad\nName': 'do-not-echo-secret'}})
        assert invalid.status_code == 422 and 'do-not-echo-secret' not in invalid.text
        assert (await client.put('/api/skills/' + skill_id + '/enabled', json={'enabled': False})).status_code == 200
        assert (await client.delete('/api/skills/' + skill_id)).json()['deleted']
