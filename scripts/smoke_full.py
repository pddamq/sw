"""Verify the relocated Full Preview archive through the actual frozen application's API.

Only executor initialization, tool schemas and sw_status are requested. No CAD mutation or SW launch.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import secrets
import socket
import subprocess
import tempfile
import time
import zipfile

import httpx

import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from backend import __version__
from backend.blender_bundle import verify_bundle


def main():
    archive = ROOT / 'dist' / f'FormaCAD-Full-Preview-{__version__}-x64.zip'
    with tempfile.TemporaryDirectory(prefix='full-exe-smoke-', dir=ROOT / 'build') as folder:
        directory = Path(folder)
        with zipfile.ZipFile(archive) as bundle:
            bundle.extractall(directory)
        app = directory / 'FormaCAD'
        runtime = app / '_internal' / 'resources' / 'mcp'
        blender_folder = app / '_internal' / 'resources' / 'blender'
        blender_manifest = verify_bundle(blender_folder)
        expected = runtime / 'solidworks-mcp.exe'
        catalog = json.loads((runtime / 'tool-catalog.json').read_text('utf-8'))
        expected_names = {tool['name'] for tool in catalog['tools']}
        assert len(expected_names) == catalog['tool_count'] == 161
        assert catalog['excluded_tools'] == []
        assert {'sim_static', 'sim_run', 'sim_plot', 'sim_list', 'sim_delete', 'feature_recognize', 'vba_run'} <= expected_names
        for name, checksum in catalog['assembly_sha256'].items():
            assert hashlib.sha256((runtime / name).read_bytes()).hexdigest() == checksum
        assert not list(runtime.glob('SolidWorks.Interop.*.dll')), 'Compile-only references must not be copied into the runtime'
        token = secrets.token_urlsafe(24)
        with socket.socket() as socket_:
            socket_.bind(('127.0.0.1', 0))
            port = socket_.getsockname()[1]
        env = dict(os.environ, FORMA_DATA_DIR=str(directory / 'profile'), FORMA_TOKEN=token,
                   TEMP=str(ROOT / 'build' / 'temp'), TMP=str(ROOT / 'build' / 'temp'))
        env.pop('FORMA_API_KEY', None)
        process = subprocess.Popen([str(app / 'FormaCAD.exe'), '--headless', '--port', str(port)], env=env)
        try:
            with httpx.Client(base_url=f'http://127.0.0.1:{port}/api',
                             headers={'Authorization': 'Bearer ' + token}, timeout=40) as client:
                deadline = time.monotonic() + 25
                while True:
                    try:
                        health = client.get('/health')
                        if health.status_code == 200 and not health.json().get('executor_starting'):
                            break
                    except httpx.ConnectError:
                        pass
                    if process.poll() is not None or time.monotonic() > deadline:
                        raise RuntimeError('Relocated frozen app did not start')
                    time.sleep(0.1)
                version_report = client.get('/diagnostics')
                version_report.raise_for_status()
                assert version_report.json()['app_version'] == __version__
                blender_status = client.get('/blender/status')
                blender_status.raise_for_status()
                assert blender_status.json()['bundle_available']
                assert blender_status.json()['scene_ready'] is False
                preset = client.get('/mcp/presets/blender')
                preset.raise_for_status()
                assert preset.json()['bundled'] == 'blender'
                assert Path(preset.json()['command']).resolve() == (blender_folder / 'server' / 'forma-blender-mcp.exe').resolve()
                addon = client.get('/blender/addon')
                addon.raise_for_status()
                assert hashlib.sha256(addon.content).hexdigest() == blender_manifest['files']['forma-blender-addon.zip']
                settings = client.get('/settings').json()
                assert Path(settings['mcp_command']).resolve() == expected.resolve()
                assert settings['allow_vba'] is False and settings['auto_connect'] is True
                assert settings['executor_kind'] == 'full'
                initial = client.get('/health').json()
                assert initial['mcp']['running'] and initial['mcp']['tools'] == 161, 'Bundled executor must auto-connect on first launch'
                setup = client.get('/setup')
                setup.raise_for_status()
                assert setup.json()['webview_repair']['available']
                assert setup.json()['bundled_full_available']
                recovered = client.post('/setup/use-bundled')
                recovered.raise_for_status()
                assert recovered.json()['settings_saved'] and recovered.json()['tools'] == 161
                candidate = next(item for item in setup.json()['executors'] if item['command'] == str(expected))
                assert candidate['usable'] and candidate['runtime_mode'] == 'self-contained'
                for enabled in (False, True, False):
                    client.put('/settings', json={'allow_vba': enabled}).raise_for_status()
                    connected = client.post('/mcp/connect')
                    connected.raise_for_status()
                    assert connected.json()['running'] and connected.json()['tools'] == 161
                    tools = client.get('/tools').json()
                    assert len(tools) == 161 and {tool['name'] for tool in tools} == expected_names
                    state = client.post('/solidworks/status')
                    state.raise_for_status()
                    assert state.json()['data']['vbaEnabled'] is enabled
                    assert not client.get('/health').json()['mcp_needs_reconnect']
                    client.put('/settings', json={'allow_vba': not enabled}).raise_for_status()
                    assert client.get('/health').json()['mcp_needs_reconnect']
                    client.post('/mcp/disconnect').raise_for_status()
                assert not client.get('/health').json()['mcp']['running']
                print(json.dumps({'archive': archive.name, 'tool_count': 161, 'catalog_complete': True,
                    'relocated_frozen_connection': 'passed', 'macro_opt_in_and_reconnect': 'passed',
                    'cad_mutations': 0, 'sw_launch_requested': False}))
        finally:
            process.terminate()
            process.wait(timeout=15)


if __name__ == '__main__':
    main()
