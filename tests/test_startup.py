import asyncio
from pathlib import Path

import httpx
import pytest

from backend.app import create_app
from backend.config import Settings, SettingsStore, is_bundled_executor
from backend.storage import Database


class StartupBridge:
    def __init__(self, fail=False):
        self.running = False
        self.tools = []
        self.sw_status = {}
        self.detail = 'fixture'
        self.calls = []
        self.connected = asyncio.Event()
        self.closed = False
        self.fail = fail
    async def connect(self, settings):
        self.calls.append(('connect', settings.mcp_command))
        if self.fail:
            self.connected.set()
            raise RuntimeError('startup fixture failure')
        self.running = True
        self.tools = [{'name': 'sw_status'}, {'name': 'sw_connect'}]
        self.connected.set()
        return {'running': True, 'tools': 2}
    async def call(self, name, arguments):
        self.calls.append((name, arguments))
        self.sw_status = {'attached': False, 'note': 'fixture only'}
        return {'data': self.sw_status, 'content': 'offline fixture', 'is_error': name == 'sw_connect'}
    async def disconnect(self):
        self.running = False
        self.closed = True


def fake_bundle(tmp_path, monkeypatch):
    import backend.config as config
    root = tmp_path / 'app' / '_internal'
    exe = root / 'resources' / 'mcp' / 'solidworks-mcp.exe'
    exe.parent.mkdir(parents=True)
    exe.write_bytes(b'fixture - never launched')
    monkeypatch.setattr(config, 'ROOT', root)
    return exe


async def test_only_exact_bundled_executor_is_auto_connected(tmp_path, monkeypatch):
    exe = fake_bundle(tmp_path, monkeypatch)
    settings = SettingsStore(tmp_path / 'settings.json')
    bridge = StartupBridge()
    app = create_app(Database(tmp_path / 'db'), settings, bridge, token='fixture')
    async with app.router.lifespan_context(app):
        await asyncio.wait_for(bridge.connected.wait(), 2)
        assert bridge.running
        assert ('sw_connect', {'launch': False}) in bridge.calls
        assert ('connect', str(exe)) in bridge.calls
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url='http://testserver',
                                     headers={'Authorization': 'Bearer fixture'}) as api:
            state = (await api.get('/api/health')).json()
            assert not state['executor_starting'] and not state['task_running']
    assert bridge.closed


@pytest.mark.parametrize('mode', ['disabled', 'external', 'arguments'])
async def test_custom_or_disabled_executor_is_never_auto_started(tmp_path, monkeypatch, mode):
    fake_bundle(tmp_path, monkeypatch)
    settings = SettingsStore(tmp_path / 'settings.json')
    if mode == 'disabled':
        settings.value.auto_connect = False
    elif mode == 'external':
        settings.value.mcp_command = str(tmp_path / 'external' / 'solidworks-mcp.exe')
    else:
        settings.value.mcp_args = ['--custom']
    bridge = StartupBridge()
    app = create_app(Database(tmp_path / 'db'), settings, bridge)
    async with app.router.lifespan_context(app):
        await asyncio.sleep(0)
        assert not bridge.calls


async def test_auto_connection_failure_keeps_app_available(tmp_path, monkeypatch):
    fake_bundle(tmp_path, monkeypatch)
    settings = SettingsStore(tmp_path / 'settings.json')
    bridge = StartupBridge(fail=True)
    app = create_app(Database(tmp_path / 'db'), settings, bridge, token='fixture')
    async with app.router.lifespan_context(app):
        await asyncio.wait_for(bridge.connected.wait(), 2)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url='http://testserver',
                                     headers={'Authorization': 'Bearer fixture'}) as api:
            response = await api.get('/api/health')
            assert response.status_code == 200
            assert not response.json()['executor_starting'] and not response.json()['task_running']
            assert (await api.get('/api/settings')).status_code == 200


def test_exact_bundled_layout_and_existing_file_are_required(tmp_path, monkeypatch):
    exe = fake_bundle(tmp_path, monkeypatch)
    assert is_bundled_executor(Settings(mcp_command=str(exe)))
    assert not is_bundled_executor(Settings(mcp_command='solidworks-mcp.exe'))
    assert not is_bundled_executor(Settings(mcp_command=str(exe), mcp_args=['--custom']))
    assert not is_bundled_executor(Settings(mcp_command=str(tmp_path / 'resources' / 'mcp' / exe.name)))
