"""Extract the BASIC archive and verify its real bundled C# through the frozen API.

Only protocol, status and help are requested. No SW launch or CAD mutation occurs.
"""
from __future__ import annotations

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

ROOT = Path(__file__).resolve().parents[1]


def main():
    archive = ROOT / 'dist' / 'FormaCAD-Basic-Preview-0.5.0-x64.zip'
    with tempfile.TemporaryDirectory(prefix='builtin-exe-smoke-', dir=ROOT / 'build') as folder:
        directory = Path(folder)
        with zipfile.ZipFile(archive) as bundle:
            bundle.extractall(directory)
        app = directory / 'FormaCAD'
        expected = app / '_internal' / 'resources' / 'builtin-mcp' / 'forma-mcp.exe'
        assert expected.is_file() and expected.with_name(expected.name + '.config').is_file()
        profile = directory / 'profile'
        token = secrets.token_urlsafe(24)
        with socket.socket() as socket_:
            socket_.bind(('127.0.0.1', 0))
            port = socket_.getsockname()[1]
        env = dict(os.environ, FORMA_DATA_DIR=str(profile), FORMA_TOKEN=token,
                   TEMP=str(ROOT / 'build' / 'temp'), TMP=str(ROOT / 'build' / 'temp'))
        env.pop('FORMA_API_KEY', None)
        process = subprocess.Popen([str(app / 'FormaCAD.exe'), '--headless', '--port', str(port)], env=env)
        try:
            with httpx.Client(base_url=f'http://127.0.0.1:{port}/api',
                             headers={'Authorization': 'Bearer ' + token}, timeout=40) as client:
                deadline = time.monotonic() + 25
                while True:
                    try:
                        if client.get('/health').status_code == 200:
                            break
                    except httpx.ConnectError:
                        pass
                    if process.poll() is not None or time.monotonic() > deadline:
                        raise RuntimeError('Extracted frozen app did not start')
                    time.sleep(0.1)
                settings = client.get('/settings').json()
                assert Path(settings['mcp_command']).resolve() == expected.resolve()
                setup = client.get('/setup')
                setup.raise_for_status()
                candidate = next(item for item in setup.json()['executors'] if item['runtime_mode'] == 'netfx-4.8')
                assert candidate['usable']
                for _ in range(2):
                    connected = client.post('/mcp/connect')
                    connected.raise_for_status()
                    assert connected.json()['running'] and connected.json()['tools'] == 18
                    tools = client.get('/tools').json()
                    assert len(tools) == 18 and 'feature_extrude' in {item['name'] for item in tools}
                    status = client.post('/solidworks/status')
                    status.raise_for_status()
                    assert status.json()['data']['executor'] == 'forma-built-in-basic'
                    assert status.json()['data']['cad_live_validated'] is False
                    assert isinstance(status.json()['data']['attached'], bool)
                    client.post('/mcp/disconnect').raise_for_status()
                assert not client.get('/health').json()['mcp']['running']
                print(json.dumps({'archive': archive.name, 'executor': expected.name, 'tools': 18,
                                  'relocated_frozen_connection': 'passed', 'reconnect': 'passed',
                                  'cad_mutations': 0, 'sw_launch_requested': False}))
        finally:
            process.terminate()
            process.wait(timeout=15)


if __name__ == '__main__':
    main()
