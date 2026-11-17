import hashlib
import json
from pathlib import Path
import subprocess
from types import SimpleNamespace as NS

import pytest

from backend.prerequisites import (DesktopDependencyError, bootstrapper_info, ensure_desktop_runtime,
                                   install_webview, startup_report)


def payload(root: Path) -> Path:
    folder = root / 'resources' / 'prerequisites'
    folder.mkdir(parents=True)
    content = b'MZ' + b'fixture-not-executable' * 80
    exe = folder / 'MicrosoftEdgeWebview2Setup.exe'
    exe.write_bytes(content)
    (folder / 'webview2-bootstrapper.json').write_text(json.dumps({
        'file': exe.name, 'bytes': len(content), 'sha256': hashlib.sha256(content).hexdigest(),
        'signature': {'status': 'Valid', 'subject': 'CN=Microsoft Corporation, O=Microsoft Corporation'},
    }), 'utf-8')
    return exe


def test_bootstrapper_validation_is_read_only(tmp_path):
    exe = payload(tmp_path)
    assert bootstrapper_info(tmp_path)['available']
    original = exe.read_bytes()
    assert exe.read_bytes() == original
    exe.write_bytes(original + b'tampered')
    assert not bootstrapper_info(tmp_path)['available']


@pytest.mark.parametrize('change', ['missing', 'invalid-json', 'wrong-name', 'wrong-publisher', 'bad-signature', 'oversized', 'wrong-hash'])
def test_invalid_bootstrapper_is_never_executed(tmp_path, change):
    exe = payload(tmp_path)
    manifest = exe.parent / 'webview2-bootstrapper.json'
    data = json.loads(manifest.read_text('utf-8'))
    if change == 'missing':
        exe.unlink()
    elif change == 'invalid-json':
        manifest.write_text('{broken', 'utf-8')
    elif change == 'oversized':
        manifest.write_text(' ' * 16001, 'utf-8')
    else:
        if change == 'wrong-name': data['file'] = 'other.exe'
        if change == 'wrong-publisher': data['signature']['subject'] = 'CN=Microsoft Corporation Fake, O=Other'
        if change == 'bad-signature': data['signature']['status'] = 'UnknownError'
        if change == 'wrong-hash': data['sha256'] = '0' * 64
        manifest.write_text(json.dumps(data), 'utf-8')
    calls = []
    with pytest.raises(DesktopDependencyError, match='校验失败'):
        install_webview(tmp_path, run=lambda *args, **kwargs: calls.append(args))
    assert not calls


def test_webview_installer_runs_explicit_command_then_verifies_real_state(tmp_path, monkeypatch):
    exe = payload(tmp_path)
    calls = []
    states = iter([{'webview2': None}, {'webview2': None}, {'webview2': '142.0.1.0'}])
    monkeypatch.setenv('FORMA_API_KEY', 'never-send-to-bootstrapper')
    def run(command, **kwargs):
        calls.append((command, kwargs))
        return NS(returncode=0)
    result = install_webview(tmp_path, run=run, inventory=lambda: next(states), wait=lambda _: None)
    assert result['installed'] and not result['already_present']
    assert calls[0][0] == [str(exe), '/silent', '/install']
    assert calls[0][1]['timeout'] == 600
    assert 'FORMA_API_KEY' not in calls[0][1]['env']


@pytest.mark.parametrize('exit_code', [0, 42, 3010, 1641])
def test_installer_exit_code_is_not_proof_runtime_is_ready(tmp_path, exit_code):
    payload(tmp_path)
    with pytest.raises(DesktopDependencyError, match='重启' if exit_code in {3010, 1641} else '尚未就绪'):
        install_webview(tmp_path, run=lambda *args, **kwargs: NS(returncode=exit_code),
                        inventory=lambda: {'webview2': None}, wait=lambda _: None)


def test_already_installed_runtime_never_runs_bootstrapper(tmp_path):
    payload(tmp_path)
    def fail(*args, **kwargs):
        pytest.fail('Already installed runtime must not launch an installer')
    assert install_webview(tmp_path, run=fail, inventory=lambda: {'webview2': '142.0.1.0'})['already_present']


def test_installer_timeout_is_reported_without_claiming_rollback(tmp_path):
    payload(tmp_path)
    def timeout(*args, **kwargs):
        raise subprocess.TimeoutExpired(args[0], 600)
    with pytest.raises(DesktopDependencyError, match='后台继续'):
        install_webview(tmp_path, run=timeout, inventory=lambda: {'webview2': None})


@pytest.fixture
def windows(monkeypatch):
    import backend.prerequisites as module
    monkeypatch.setattr(module.sys, 'platform', 'win32')
    monkeypatch.setattr(module, 'bootstrapper_info', lambda *args: {'available': True})
    return module


def test_smoke_mode_does_not_prompt_or_install(windows):
    calls = []
    with pytest.raises(DesktopDependencyError, match='测试模式不会自动安装'):
        ensure_desktop_runtime(interactive=False, inventory=lambda: {'netfx_release': 528040, 'webview2': None},
            confirm=lambda *args: calls.append('confirm'), install=lambda: calls.append('install'))
    assert not calls


def test_missing_netfx_gives_specific_system_component_message(windows):
    with pytest.raises(DesktopDependencyError, match='无需安装 .NET SDK'):
        ensure_desktop_runtime(inventory=lambda: {'netfx_release': 0, 'webview2': None},
                               confirm=lambda *args: pytest.fail('Must not install WebView2 first'))


def test_declining_repair_never_launches_installer(windows):
    with pytest.raises(DesktopDependencyError, match='已取消'):
        ensure_desktop_runtime(inventory=lambda: {'netfx_release': 528040, 'webview2': None},
            confirm=lambda *args: False, install=lambda: pytest.fail('User declined installation'))


def test_approved_repair_rechecks_runtime(windows):
    states = iter([{'netfx_release': 528040, 'webview2': None}, {'netfx_release': 528040, 'webview2': '142.0.1.0'}])
    calls = []
    ensure_desktop_runtime(inventory=lambda: next(states), confirm=lambda *args: True, install=lambda: calls.append('install'))
    assert calls == ['install']


def test_read_only_runtime_report_never_contains_credentials(windows, monkeypatch):
    monkeypatch.setenv('FORMA_API_KEY', 'secret-runtime-check')
    monkeypatch.setattr(windows, 'windows_inventory', lambda: {'webview2': '142.0.1.0', 'netfx_release': 528040, 'com_registered': False})
    report = startup_report()
    assert report['desktop_runtime_ready'] and not report['cad_installed']
    assert 'secret-runtime-check' not in json.dumps(report)


def test_runtime_check_cli_bypasses_webview_server_and_executor(tmp_path, monkeypatch):
    import desktop
    report = tmp_path / 'report.json'
    monkeypatch.setattr(desktop.sys, 'argv', ['FormaCAD.exe', '--check-runtime', str(report)])
    monkeypatch.setattr(desktop, 'configure_logging', lambda: None)
    monkeypatch.setattr(desktop, 'startup_report', lambda: {'desktop_runtime_ready': False, 'fixture': True})
    monkeypatch.setattr(desktop, 'create_app', lambda: pytest.fail('Diagnostics must not create an API app'))
    monkeypatch.setattr(desktop, 'ensure_desktop_runtime', lambda **kwargs: pytest.fail('Diagnostics must not install prerequisites'))
    desktop.main()
    assert json.loads(report.read_text('utf-8')) == {'desktop_runtime_ready': False, 'fixture': True}


def test_missing_dependency_fails_before_starting_local_backend(monkeypatch):
    import desktop
    monkeypatch.setattr(desktop.sys, 'argv', ['FormaCAD.exe'])
    monkeypatch.setattr(desktop, 'configure_logging', lambda: None)
    def missing(**kwargs):
        raise DesktopDependencyError('fixture missing runtime')
    monkeypatch.setattr(desktop, 'ensure_desktop_runtime', missing)
    monkeypatch.setattr(desktop, 'create_app', lambda: pytest.fail('Missing prerequisites must not leave a running executor'))
    with pytest.raises(DesktopDependencyError, match='fixture missing'):
        desktop.main()
