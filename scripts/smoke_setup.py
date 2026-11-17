"""Verify setup diagnostics and corrupt-config recovery in the frozen desktop application."""
from __future__ import annotations

import json
import os
from pathlib import Path
import secrets
import socket
import subprocess
import tempfile
import time

import httpx

ROOT = Path(__file__).resolve().parents[1]


def main():
    with tempfile.TemporaryDirectory(prefix='setup-exe-smoke-', dir=ROOT / 'build') as folder:
        profile = Path(folder)
        broken = '{ deliberately broken configuration'
        (profile / 'settings.json').write_text(broken, 'utf-8')
        (profile / 'settings.bak').write_text(json.dumps({'model': 'recovery-fixture'}), 'utf-8')
        with socket.socket() as socket_:
            socket_.bind(('127.0.0.1', 0))
            port = socket_.getsockname()[1]
        token = secrets.token_urlsafe(24)
        env = dict(os.environ, FORMA_TOKEN=token, FORMA_DATA_DIR=str(profile),
                   TEMP=str(ROOT / 'build' / 'temp'), TMP=str(ROOT / 'build' / 'temp'))
        env.pop('FORMA_API_KEY', None)
        process = subprocess.Popen([str(ROOT / 'dist' / 'FormaCAD' / 'FormaCAD.exe'), '--headless', '--port', str(port)], env=env)
        try:
            with httpx.Client(base_url=f'http://127.0.0.1:{port}/api', headers={'Authorization': 'Bearer ' + token}, timeout=20) as client:
                deadline = time.monotonic() + 20
                while True:
                    try:
                        health = client.get('/health')
                        if health.status_code == 200 and not health.json().get('executor_starting'):
                            break
                    except httpx.ConnectError:
                        pass
                    if process.poll() is not None or time.monotonic() > deadline:
                        raise RuntimeError('Frozen setup test backend failed to start')
                    time.sleep(0.1)
                settings = client.get('/settings').json()
                assert settings['model'] == 'recovery-fixture' and settings['configuration_warnings']
                assert (profile / 'settings.json').read_text('utf-8') == broken
                report = client.get('/setup')
                report.raise_for_status()
                assert {'model', 'executor', 'cad'} <= {item['id'] for item in report.json()['checks']}
                assert 'encrypted_api_key' not in report.text
                repaired = client.put('/settings', json={'model': 'repaired-fixture', 'clear_api_key': True})
                repaired.raise_for_status()
                assert not repaired.json()['configuration_warnings']
                archived = list(profile.glob('settings.recovery-*.json'))
                assert len(archived) == 1 and archived[0].read_text('utf-8') == broken
                assert client.post('/setup/apply-executor', json={'id': 'not-a-candidate'}).status_code == 404
            print('Frozen setup diagnostics / backup recovery / repair / archive preservation passed. No CAD was launched.')
        finally:
            process.terminate()
            process.wait(timeout=15)


if __name__ == '__main__':
    main()
