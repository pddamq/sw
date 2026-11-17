import json
from pathlib import Path
from types import SimpleNamespace as NS

import httpx
import pytest

from backend.app import create_app
from backend.config import SettingsStore
from backend.storage import Database


def test_bundle_marker_follows_current_extraction_even_when_old_file_exists(tmp_path, monkeypatch):
    import backend.config as module
    old = tmp_path / 'old' / '_internal'
    current = tmp_path / 'new' / '_internal'
    paths = [root / 'resources' / 'mcp' / 'solidworks-mcp.exe' for root in (old, current)]
    for path in paths:
        path.parent.mkdir(parents=True)
        path.write_bytes(b'fixture only')
    monkeypatch.setattr(module, 'ROOT', old)
    config_path = tmp_path / 'profile' / 'settings.json'
    store = SettingsStore(config_path)
    store.update({'model': 'retained', 'mcp_cwd': str(paths[0].parent)})
    assert json.loads(config_path.read_text('utf-8'))['_bundled_executor'] == 'full'
    monkeypatch.setattr(module, 'ROOT', current)
    relocated = SettingsStore(config_path)
    assert relocated.value.mcp_command == str(paths[1])
    assert relocated.value.mcp_cwd == str(paths[1].parent)
    assert relocated.value.model == 'retained' and relocated.public()['executor_kind'] == 'full'
    assert '_bundled_executor' not in relocated.public()
    assert json.loads(config_path.read_text('utf-8'))['mcp_command'] == str(paths[0])


def test_marker_never_rewrites_custom_commands_or_arguments(tmp_path, monkeypatch):
    import backend.config as module
    root = tmp_path / 'bundle'
    exe = root / 'resources' / 'mcp' / 'solidworks-mcp.exe'
    exe.parent.mkdir(parents=True)
    exe.write_bytes(b'fixture')
    monkeypatch.setattr(module, 'ROOT', root)
    for command, args in [(str(tmp_path / 'custom' / 'service.exe'), []), (str(exe), ['--custom'])]:
        path = tmp_path / 'settings.json'
        path.write_text(json.dumps({'mcp_command': command, 'mcp_args': args, '_bundled_executor': 'full'}), 'utf-8')
        loaded = SettingsStore(path)
        assert loaded.value.mcp_command == command and loaded.value.mcp_args == args
        assert not loaded.public()['executor_kind']


@pytest.mark.parametrize('fail', [False, True])
async def test_restore_bundled_endpoint_requires_auth_and_retains_model(tmp_path, monkeypatch, fail):
    import backend.app as module
    import backend.config as config_module
    root = tmp_path / 'app'
    exe = root / 'resources' / 'mcp' / 'solidworks-mcp.exe'
    exe.parent.mkdir(parents=True)
    exe.write_bytes(b'fixture-never-launched')
    monkeypatch.setattr(module, 'ROOT', root)
    monkeypatch.setattr(config_module, 'ROOT', root)
    settings = SettingsStore(tmp_path / 'settings.json')
    settings.update({'model': 'kept-model', 'mcp_command': 'old-custom-executor', 'mcp_args': ['old']})
    bridge = NS(running=False, tools=[], sw_status={}, detail='fixture')
    calls = []
    async def connect(value):
        calls.append(value.model_dump())
        if fail:
            raise RuntimeError('fixture startup error')
        return {'running': True, 'tools': 0}
    bridge.connect = connect
    app = create_app(Database(tmp_path / 'app.db'), settings, bridge, token='fixture')
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url='http://testserver') as api:
        assert (await api.post('/api/setup/use-bundled')).status_code == 401
        assert not calls
        api.headers['Authorization'] = 'Bearer fixture'
        result = await api.post('/api/setup/use-bundled')
        assert result.status_code == (400 if fail else 200)
        assert settings.value.model == 'kept-model'
        assert settings.value.mcp_command == str(exe) and settings.value.mcp_args == []
        assert calls[0]['mcp_command'] == str(exe)
        if fail:
            assert '配置已保存' in result.json()['detail']
        assert json.loads(settings.path.with_suffix('.bak').read_text('utf-8'))['mcp_command'] == 'old-custom-executor'


async def test_restore_bundled_endpoint_does_not_save_when_payload_missing(tmp_path, monkeypatch):
    import backend.app as module
    monkeypatch.setattr(module, 'ROOT', tmp_path)
    settings = SettingsStore(tmp_path / 'settings.json')
    previous = settings.value.model_dump()
    app = create_app(Database(tmp_path / 'db'), settings, token='fixture')
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url='http://testserver',
                                 headers={'Authorization': 'Bearer fixture'}) as api:
        assert (await api.post('/api/setup/use-bundled')).status_code == 404
        assert settings.value.model_dump() == previous
