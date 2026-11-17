import json
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest

from backend.app import create_app
from backend.config import Settings, SettingsStore
from backend.mcp_bridge import McpBridge
from backend.setup import discover_executors, runtime_requirements, setup_report
from backend.storage import Database


DOTNET_ABSENT = {'command': None, 'runtimes': [], 'sdks': [], 'detail': ''}


def test_configuration_backup_and_recovery_preserves_corrupted_file(tmp_path):
    path = tmp_path / 'settings.json'
    store = SettingsStore(path)
    store.update({'model': 'previous-model'})
    store.update({'model': 'latest-model'})
    path.write_text('{ broken json', 'utf-8')
    recovered = SettingsStore(path)
    assert recovered.value.model == 'previous-model'
    assert recovered.recovery_pending and recovered.public()['configuration_warnings']
    assert path.read_text('utf-8') == '{ broken json'
    recovered.update({'model': 'repaired'})
    assert json.loads(path.read_text('utf-8'))['model'] == 'repaired'
    backups = list(tmp_path.glob('settings.recovery-*.json'))
    assert len(backups) == 1 and backups[0].read_text('utf-8') == '{ broken json'
    assert not recovered.public()['configuration_warnings']


def test_invalid_configuration_can_start_with_defaults(tmp_path, monkeypatch):
    monkeypatch.delenv('FORMA_API_KEY', raising=False)
    path = tmp_path / 'settings.json'
    path.write_text('[]', 'utf-8')
    store = SettingsStore(path)
    assert store.value.model == 'gpt-4.1'
    assert store.public()['configuration_warnings']
    assert not store.public()['has_api_key']


def test_unreadable_credential_does_not_break_configuration(tmp_path, monkeypatch):
    import backend.config as module
    monkeypatch.delenv('FORMA_API_KEY', raising=False)
    path = tmp_path / 'settings.json'
    path.write_text(json.dumps({'model': 'preserved-model', 'encrypted_api_key': 'corrupt-encrypted-key'}), 'utf-8')
    def broken(*args, **kwargs):
        raise ValueError('DPAPI fixture error')
    monkeypatch.setattr(module, 'protect', broken)
    store = SettingsStore(path)
    assert store.value.model == 'preserved-model'
    assert store.public()['credential_unreadable'] and not store.public()['has_api_key']
    assert store.key() == ''
    assert 'corrupt-encrypted-key' not in json.dumps(store.public())
    store.update({'clear_api_key': True})
    assert not store.public()['credential_unreadable']


@pytest.mark.parametrize('url', [' ', 'https://', 'file:///a', 'https://user:password@example.com/v1',
                                 'https://example.com/v1?api_key=secret', 'https://example.com:invalid/v1'])
def test_invalid_provider_urls_are_rejected(url):
    with pytest.raises(ValueError):
        Settings(base_url=url)


def test_quoted_mcp_path_is_normalized():
    assert Settings(mcp_command=' "C:\\MCP Server\\solidworks-mcp.exe" ').mcp_command == 'C:\\MCP Server\\solidworks-mcp.exe'
    assert Settings(embedding_base_url='').embedding_base_url == ''
    with pytest.raises(ValueError):
        Settings(model='  ')


def test_discovery_is_read_only_and_classifies_runtime_dependencies(tmp_path, monkeypatch):
    root, local = tmp_path / 'app', tmp_path / 'local'
    bundled = root / 'resources' / 'mcp'
    bundled.mkdir(parents=True)
    exe = bundled / 'solidworks-mcp.exe'
    exe.write_bytes(b'fixture - not executable')
    exe.with_suffix('.runtimeconfig.json').write_text(json.dumps({'runtimeOptions': {
        'includedFrameworks': [{'name': 'Microsoft.NETCore.App', 'version': '10.0.0'}]}}), 'utf-8')
    external = local / 'solidworks-mcp' / 'bin' / 'dev'
    external.mkdir(parents=True)
    dll = external / 'solidworks-mcp.dll'
    dll.write_bytes(b'fixture')
    monkeypatch.setattr('backend.setup.shutil.which', lambda name: None)
    items = discover_executors(root, local, DOTNET_ABSENT)
    own = next(item for item in items if item['command'] == str(exe.resolve()))
    assert own['usable'] and own['runtime_mode'] == 'self-contained'
    dll_item = next(item for item in items if item['args'])
    assert not dll_item['usable'] and 'dotnet' in dll_item['detail']
    assert exe.read_bytes() == b'fixture - not executable'


def test_framework_dependent_executor_checks_runtime(tmp_path):
    exe = tmp_path / 'solidworks-mcp.exe'
    exe.write_bytes(b'fixture')
    config = exe.with_suffix('.runtimeconfig.json')
    config.write_text(json.dumps({'runtimeOptions': {'framework': {'name': 'Microsoft.NETCore.App', 'version': '10.0.0'}}}), 'utf-8')
    assert runtime_requirements(exe, DOTNET_ABSENT)[0] is False
    available = {'command': 'dotnet', 'runtimes': [{'name': 'Microsoft.NETCore.App', 'version': '10.0.13'}], 'sdks': []}
    assert runtime_requirements(exe, available)[0] is True
    config.write_text('{"runtimeOptions":{"frameworks":"bad"}}', 'utf-8')
    assert runtime_requirements(exe, available)[2] == 'invalid'


async def test_setup_requires_auth_and_apply_does_not_run_executor(tmp_path, monkeypatch):
    import backend.app as module
    db = Database(tmp_path / 'app.db')
    config = SettingsStore(tmp_path / 'settings.json')
    bridge = McpBridge()
    calls = []
    async def connect(settings):
        calls.append('connect')
    monkeypatch.setattr(bridge, 'connect', connect)
    candidate = {'id': 'fixture-candidate', 'command': 'fixture-mcp', 'args': ['arg'],
                 'cwd': str(tmp_path), 'usable': True, 'detail': 'fixture'}
    monkeypatch.setattr(module, 'setup_report', lambda settings, bridge: {'executors': [candidate], 'checks': []})
    app = create_app(db, config, bridge, token='fixture')
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url='http://testserver') as client:
        assert (await client.get('/api/setup')).status_code == 401
        client.headers['Authorization'] = 'Bearer fixture'
        assert (await client.get('/api/setup')).status_code == 200
        assert (await client.post('/api/setup/apply-executor', json={'id': 'missing'})).status_code == 404
        response = await client.post('/api/setup/apply-executor', json={'id': candidate['id']})
        assert response.status_code == 200 and response.json()['mcp_command'] == 'fixture-mcp'
        assert config.value.mcp_args == ['arg'] and not calls
        candidate['usable'] = False
        assert (await client.post('/api/setup/apply-executor', json={'id': candidate['id']})).status_code == 400


def test_setup_report_distinguishes_registration_from_live_connection(tmp_path, monkeypatch):
    monkeypatch.setattr('backend.setup.dotnet_inventory', lambda: DOTNET_ABSENT)
    monkeypatch.setattr('backend.setup.windows_inventory', lambda: {'solidworks': [], 'com_registered': True,
                                                                   'webview2': 'fixture', 'netfx_release': 533325})
    monkeypatch.setattr('backend.setup.discover_executors', lambda **kwargs: [])
    settings = SettingsStore(tmp_path / 'settings.json')
    bridge = SimpleNamespace(running=False, tools=[], sw_status={})
    result = setup_report(settings, bridge)
    cad = next(c for c in result['checks'] if c['id'] == 'cad')
    assert cad['status'] == 'installed'
    assert not result['build']['sdk_available']
    assert 'encrypted_api_key' not in json.dumps(result)
