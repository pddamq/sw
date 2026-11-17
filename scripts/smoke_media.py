"""Exercise image endpoints in the actual frozen executable without contacting a model or CAD."""
from __future__ import annotations

import io
import os
from pathlib import Path
import secrets
import socket
import subprocess
import time

import httpx
from PIL import Image

ROOT = Path(__file__).resolve().parents[1]


def main():
    with socket.socket() as listener:
        listener.bind(('127.0.0.1', 0))
        port = listener.getsockname()[1]
    token = secrets.token_urlsafe(24)
    env = dict(os.environ, FORMA_TOKEN=token, FORMA_DATA_DIR=str(ROOT / 'build' / 'media-exe-smoke'),
               TEMP=str(ROOT / 'build' / 'temp'), TMP=str(ROOT / 'build' / 'temp'))
    process = subprocess.Popen([str(ROOT / 'dist' / 'FormaCAD' / 'FormaCAD.exe'), '--headless', '--port', str(port)], env=env)
    try:
        with httpx.Client(base_url=f'http://127.0.0.1:{port}/api', headers={'Authorization': 'Bearer ' + token}, timeout=10) as client:
            deadline = time.monotonic() + 20
            while True:
                try:
                    health = client.get('/health')
                    if health.status_code == 200 and not health.json().get('executor_starting'):
                        break
                except httpx.ConnectError:
                    pass
                if process.poll() is not None or time.monotonic() > deadline:
                    raise RuntimeError('Packaged media smoke backend did not start')
                time.sleep(0.1)
            session = client.post('/sessions', json={'title': 'frozen image test, no CAD'}).json()
            stream = io.BytesIO()
            Image.new('RGB', (180, 120), '#506070').save(stream, format='PNG')
            uploaded = client.post('/sessions/' + session['id'] + '/attachments',
                                   files={'file': ('fixture.png', stream.getvalue(), 'image/png')})
            uploaded.raise_for_status()
            image = uploaded.json()
            response = client.get('/media/' + image['id'])
            assert response.status_code == 200 and response.content.startswith(b'\x89PNG')
            assert image['width'] == 180 and image['height'] == 120
            invalid = client.post('/sessions/' + session['id'] + '/attachments', files={'file': ('invalid.svg', b'<svg/>')})
            assert invalid.status_code == 400
            with httpx.Client() as anonymous:
                assert anonymous.get(f'http://127.0.0.1:{port}/api/media/' + image['id']).status_code == 401
            client.delete('/sessions/' + session['id']).raise_for_status()
            assert client.get('/media/' + image['id']).status_code == 404
        print('Frozen executable image upload / PNG decode / auth / cascade cleanup passed. No CAD or model was used.')
    finally:
        process.terminate()
        process.wait(timeout=15)


if __name__ == '__main__':
    main()
