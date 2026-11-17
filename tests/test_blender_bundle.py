"""One-click startup tests use synthetic subprocess and MCP fixtures, never a real Blender UI."""
import asyncio
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock

import httpx
import pytest
from pydantic import ValidationError

from backend.app import create_app
from backend.blender import blender_preset, bundled_command
from backend.blender_bundle import REQUIRED, verify_bundle
from backend.blender_launcher import BlenderLauncher, blender_installations
from backend.config import SettingsStore
from backend.external_mcp_config import ServerDraft, ServerStore
from backend.storage import Database
from test_agent import FakeBridge


def bundle_fixture(tmp_path, monkeypatch):
    monkeypatch.setattr('backend.blender.ROOT', tmp_path)
    folder = tmp_path / 'resources' / 'blender'
    for name in REQUIRED:
        path = folder / name; path.parent.mkdir(parents=True, exist_ok=True); path.write_bytes(b'synthetic bundle fixture')
    files = {name: hashlib.sha256((folder / name).read_bytes()).hexdigest() for name in REQUIRED}
    (folder / 'bundle.json').write_text(json.dumps({'format': 1, 'package': 'mcp-for-blender', 'version': '2.2.7', 'files': files}), 'utf-8')
    return folder


def test_bundle_hashes_missing_tampering_and_traversal(tmp_path, monkeypatch):
    folder = bundle_fixture(tmp_path, monkeypatch)
    verify_bundle(folder)
    (folder / 'addon.py').write_text('changed', 'utf-8')
    with pytest.raises(ValueError, match='校验'): verify_bundle(folder)
    folder = bundle_fixture(tmp_path, monkeypatch)
    data = json.loads((folder / 'bundle.json').read_text('utf-8'))
    data['files']['../outside'] = '0' * 64
    (folder / 'bundle.json').write_text(json.dumps(data), 'utf-8')
    with pytest.raises(ValueError): verify_bundle(folder)


def test_bundled_configuration_migrates_only_explicit_marked_targets(tmp_path, monkeypatch):
    bundle_fixture(tmp_path, monkeypatch)
    db = Database(tmp_path / 'servers.db'); store = ServerStore(db)
    preset = blender_preset()
    fields = {key: preset[key] for key in ('name', 'command', 'args', 'transport', 'bundled')}
    saved = store.save(ServerDraft(**fields))
    generic = store.save(ServerDraft(name='external', command='C:/custom/forma-blender-mcp.exe', args=['custom']))
    monkeypatch.setattr('backend.blender.ROOT', tmp_path / 'moved')
    assert store.get(saved['id'])['command'] == str(bundled_command())
    assert store.get(generic['id'])['command'] == generic['command']
    for changes in ({'command': 'other.exe'}, {'args': ['--host', 'example.com', '--port', '9876']}, {'env': {'TOKEN': 'x'}}):
        fields['command'] = str(bundled_command())
        with pytest.raises(ValidationError): ServerDraft(**{**fields, **changes})


def test_installation_discovery_supports_explicit_portable_path(tmp_path, monkeypatch):
    target = tmp_path / 'Portable Blender' / 'blender.exe'
    target.parent.mkdir(); target.write_bytes(b'synthetic executable')
    monkeypatch.setattr('backend.blender_launcher.shutil.which', lambda name: str(target))
    items = blender_installations(str(target))
    assert len([item for item in items if item['path'] == str(target.resolve())]) == 1


@pytest.fixture
def launcher_fixture(tmp_path, monkeypatch):
    bundle_fixture(tmp_path, monkeypatch)
    target = tmp_path / 'Blender' / 'blender.exe'; target.parent.mkdir(); target.write_bytes(b'fixture')
    db = Database(tmp_path / 'launcher.db'); store = ServerStore(db)
    hub = NS(connections={}, log_dir=tmp_path, public=lambda: store.list())
    hub.disconnect = AsyncMock()
    hub.connect = AsyncMock(side_effect=lambda sid: hub.connections.update({sid: NS(running=True)}))
    hub.tool_name = lambda sid, name: 'fixture_' + name
    process = NS(poll=lambda: None)
    popen = []
    def spawn(*args, **kwargs):
        popen.append((args, kwargs)); return process
    monkeypatch.setattr('backend.blender_launcher.subprocess.Popen', spawn)
    monkeypatch.setattr('backend.blender_launcher.blender_installations', lambda saved='': [{'path': str(target), 'label': 'fixture'}])
    launcher = BlenderLauncher(store, hub, tmp_path)
    launcher._wait_listener = AsyncMock()
    hub.call = AsyncMock(side_effect=lambda name, args: {'content': 'Scene ' + launcher.tag, 'is_error': False})
    return launcher, hub, store, popen, process


async def test_one_click_launches_once_configures_and_observes_without_inheriting_keys(launcher_fixture, monkeypatch):
    launcher, hub, store, popen, process = launcher_fixture
    monkeypatch.setenv('FORMA_API_KEY', 'never-pass-to-blender')
    first = await launcher.start()
    second = await launcher.start()
    assert first['connected'] and second['scene_ready']
    assert len(popen) == 1 and len(store.list()) == 1
    args, options = popen[0]
    assert '--factory-startup' in args[0] and '--disable-autoexec' in args[0]
    assert '--python' in args[0] and str(args[0][-1]).endswith('forma_start.py')
    assert 'FORMA_API_KEY' not in options['env']
    assert launcher.path_file.exists() and launcher.status()['scene_ready']
    assert store.get(first['server_id'])['bundled'] == 'blender'
    assert hub.call.await_count == 1


async def test_scene_mismatch_disconnects_without_closing_user_window(launcher_fixture):
    launcher, hub, store, popen, process = launcher_fixture
    hub.call = AsyncMock(return_value={'content': 'Some other scene', 'is_error': False})
    with pytest.raises(ValueError, match='身份核对'): await launcher.start()
    assert not launcher.ready and process.poll() is None
    assert hub.disconnect.await_count >= 1


async def test_missing_installation_has_actionable_error_without_launch(launcher_fixture, monkeypatch):
    launcher, hub, store, popen, process = launcher_fixture
    monkeypatch.setattr('backend.blender_launcher.blender_installations', lambda saved='': [])
    with pytest.raises(ValueError, match='未发现'): await launcher.start()
    assert not popen and not store.list()


async def test_launch_exit_and_timeout_are_not_scene_success(launcher_fixture):
    launcher, hub, store, popen, process = launcher_fixture
    launcher.process = NS(poll=lambda: 1); launcher.port = 12345
    with pytest.raises(ValueError, match='提前退出'): await BlenderLauncher._wait_listener(launcher, timeout=0.1)
    launcher.process = NS(poll=lambda: None)
    with pytest.raises(ValueError, match='尚未完成'): await BlenderLauncher._wait_listener(launcher, timeout=0)


async def test_start_api_requires_auth_and_delegates_without_manual_config(tmp_path, monkeypatch):
    db = Database(tmp_path / 'api.db')
    app = create_app(db, SettingsStore(tmp_path / 'settings.json'), FakeBridge(), token='fixture')
    started = AsyncMock(return_value={'connected': True, 'scene_ready': True})
    monkeypatch.setattr(app.state.blender, 'start', started)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url='http://testserver') as client:
        assert (await client.post('/api/blender/start', json={})).status_code == 401
        client.headers['Authorization'] = 'Bearer fixture'
        assert (await client.post('/api/blender/start', json={})).json()['connected']
        started.assert_awaited_once_with(None)
        assert (await client.post('/api/blender/start', json={'executable': 123})).status_code == 422


async def test_offline_addon_download_is_authenticated_and_integrity_checked(tmp_path, monkeypatch):
    folder = bundle_fixture(tmp_path, monkeypatch)
    db = Database(tmp_path / 'download.db')
    app = create_app(db, SettingsStore(tmp_path / 'settings.json'), FakeBridge(), token='fixture')
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url='http://testserver') as client:
        assert (await client.get('/api/blender/addon')).status_code == 401
        client.headers['Authorization'] = 'Bearer fixture'
        response = await client.get('/api/blender/addon')
        assert response.status_code == 200 and 'forma-blender-addon.zip' in response.headers['content-disposition']
        (folder / 'addon.py').write_text('changed', 'utf-8')
        assert (await client.get('/api/blender/addon')).status_code == 400
