"""Real compiled C# protocol checks; never start or mutate SOLIDWORKS."""
import json
import os
from pathlib import Path
import subprocess
import sys

from jsonschema import Draft202012Validator
import pytest

from backend.config import Settings, SettingsStore
from backend.mcp_bridge import McpBridge
from backend.setup import discover_executors, runtime_requirements

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / 'executor' / 'FormaMcp.cs'


@pytest.fixture(scope='module')
def executor(tmp_path_factory):
    if sys.platform != 'win32':
        pytest.skip('Built-in executor requires Windows .NET Framework 4.8')
    compiler = Path(os.environ.get('WINDIR', r'C:\Windows')) / 'Microsoft.NET' / 'Framework64' / 'v4.0.30319' / 'csc.exe'
    if not compiler.is_file():
        pytest.skip('.NET Framework compiler is absent')
    output = tmp_path_factory.mktemp('real-csharp')
    result = subprocess.run(['powershell', '-NoProfile', '-ExecutionPolicy', 'Bypass', '-File',
                             str(ROOT / 'scripts' / 'build-builtin-mcp.ps1'), '-Destination', str(output)],
                            capture_output=True, text=True, encoding='utf-8', errors='replace', timeout=30)
    assert result.returncode == 0, result.stdout + result.stderr
    return output / 'forma-mcp.exe'


def exchange(executor, messages):
    encoded = '\n'.join(json.dumps(message, ensure_ascii=False) if not isinstance(message, str) else message
                        for message in messages) + '\n'
    result = subprocess.run([str(executor)], input=encoded, encoding='utf-8', capture_output=True, timeout=15)
    assert result.returncode == 0, result.stderr
    return [json.loads(line) for line in result.stdout.splitlines()]


def rpc(method, params=None, id=1):
    return {'jsonrpc': '2.0', 'id': id, 'method': method, 'params': params or {}}


def test_compiled_version_and_utf8_jsonrpc(executor):
    result = subprocess.run([str(executor), '--version'], capture_output=True, text=True, timeout=5)
    assert result.returncode == 0 and '0.5.0' in result.stdout
    responses = exchange(executor, [rpc('initialize', {'protocolVersion': '2025-06-18'}),
        {'jsonrpc': '2.0', 'method': 'notifications/initialized'}, rpc('ping', id='中文-✓')])
    assert len(responses) == 2
    assert responses[0]['result']['serverInfo']['name'] == 'forma-built-in-basic'
    assert responses[0]['result']['protocolVersion'] == '2025-06-18'
    assert responses[1]['id'] == '中文-✓' and responses[1]['result'] == {}


def test_tool_inventory_contains_only_declared_basic_operations(executor):
    tools = exchange(executor, [rpc('tools/list')])[0]['result']['tools']
    names = {tool['name'] for tool in tools}
    assert len(names) == 18
    assert {'sw_help', 'sw_status', 'sw_connect', 'doc_save', 'sketch_circle', 'sketch_rectangle',
            'sketch_finish', 'feature_extrude', 'view_screenshot', 'model_measure'} <= names
    assert not {'vba_run', 'execute_csharp', 'asm_mate', 'sim_run'} & names
    for tool in tools:
        Draft202012Validator.check_schema(tool['inputSchema'])
        assert tool['inputSchema']['additionalProperties'] is False


@pytest.mark.parametrize('name,arguments', [
    ('sw_status', {'unknown': True}), ('sw_connect', {'launch': 'true'}),
    ('sketch_rectangle', {'x1_mm': True, 'y1_mm': 0, 'x2_mm': 10, 'y2_mm': 10}),
    ('sketch_circle', {'x_mm': 0, 'y_mm': 0}),
    ('feature_extrude', {'sketch_name': '草图1', 'depth_mm': 100001}),
    ('doc_new', {'template_path': ' '}), ('doc_open', {'path': 23}),
])
def test_csharp_validates_arguments_before_com(executor, name, arguments):
    response = exchange(executor, [rpc('tools/call', {'name': name, 'arguments': arguments})])[0]['result']
    assert response['isError'] is True
    detail = json.loads(response['content'][0]['text'])
    assert detail['success'] is False and 'SOLIDWORKS is not attached' not in detail['error']


def test_protocol_errors_do_not_kill_transport(executor):
    responses = exchange(executor, ['{broken', [], rpc('missing'),
        rpc('tools/call', {'name': 'not_a_tool'}), rpc('tools/call', {'name': 'sw_help', 'arguments': []}), rpc('ping')])
    assert [item['error']['code'] for item in responses[:-1]] == [-32700, -32600, -32601, -32602, -32602]
    assert responses[-1]['result'] == {}


def test_oversized_request_exits_without_processing(executor):
    result = subprocess.run([str(executor)], input='x' * (1024 * 1024 + 1) + '\n',
                            encoding='utf-8', capture_output=True, timeout=15)
    assert result.returncode == 1 and '1 MiB' in result.stderr
    assert not result.stdout


async def test_real_mcp_client_connects_without_sw_and_reports_honestly(executor):
    bridge = McpBridge()
    try:
        result = await bridge.connect(Settings(mcp_command=str(executor)))
        assert result['running'] and result['tools'] == 18
        state = await bridge.call('sw_status', {})
        assert state['data']['attached'] is False
        assert state['data']['executor'] == 'forma-built-in-basic'
        help_ = await bridge.call('sw_help', {})
        assert 'not the upstream full toolset' in help_['data']['limits']
        assert state['data']['cad_live_validated'] is False
        offline = await bridge.call('model_info', {})
        assert offline['is_error'] and offline['data']['success'] is False
        assert 'not attached' in offline['data']['error']
        await bridge.disconnect()
        await bridge.connect(Settings(mcp_command=str(executor)))
        assert not (await bridge.call('sw_status', {}))['data']['attached']
    finally:
        await bridge.disconnect()


def test_builtin_defaults_and_upstream_precedence(tmp_path, monkeypatch):
    import backend.config as config
    monkeypatch.setattr(config, 'ROOT', tmp_path)
    builtin = tmp_path / 'resources' / 'builtin-mcp' / 'forma-mcp.exe'
    builtin.parent.mkdir(parents=True)
    builtin.write_bytes(b'fixture, not executable')
    assert SettingsStore(tmp_path / 'a.json').value.mcp_command == str(builtin)
    upstream = tmp_path / 'resources' / 'mcp' / 'solidworks-mcp.exe'
    upstream.parent.mkdir(parents=True)
    upstream.write_bytes(b'fixture')
    assert SettingsStore(tmp_path / 'b.json').value.mcp_command == str(upstream)
    (tmp_path / 'c.json').write_text(json.dumps({'mcp_command': 'explicit-user-executor'}), 'utf-8')
    assert SettingsStore(tmp_path / 'c.json').value.mcp_command == 'explicit-user-executor'


def test_builtin_discovery_reports_netfx_not_dotnet(tmp_path, monkeypatch):
    import backend.setup as setup
    folder = tmp_path / 'resources' / 'builtin-mcp'
    folder.mkdir(parents=True)
    exe = folder / 'forma-mcp.exe'
    exe.write_bytes(b'fixture')
    exe.with_name(exe.name + '.config').write_bytes((ROOT / 'executor' / 'forma-mcp.exe.config').read_bytes())
    absent = {'command': None, 'runtimes': [], 'sdks': [], 'detail': ''}
    monkeypatch.setattr(setup.sys, 'platform', 'win32')
    monkeypatch.setattr(setup, 'windows_inventory', lambda: {'netfx_release': 528040})
    monkeypatch.setattr(setup.shutil, 'which', lambda name: None)
    candidate = next(item for item in discover_executors(tmp_path, tmp_path / 'local', absent)
                     if item['command'] == str(exe.resolve()))
    assert candidate['usable'] and candidate['runtime_mode'] == 'netfx-4.8'
    monkeypatch.setattr(setup, 'windows_inventory', lambda: {'netfx_release': None})
    assert not runtime_requirements(exe, absent)[0]
    exe.with_name(exe.name + '.config').unlink()
    assert runtime_requirements(exe, absent)[2] == 'invalid'
