"""Frozen workflow smoke: local model fixture, deny save, then read-only recovery.

Only sw_status is called on the real executor. No model provider, save, macro or CAD edit is run.
"""
from __future__ import annotations

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import secrets
import socket
import subprocess
import tempfile
import threading
import time

import httpx

ROOT = Path(__file__).resolve().parents[1]


def main():
    requests = []
    class Model(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_POST(self):
            payload = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
            requests.append(payload)
            if len(requests) == 1:
                delta = {'tool_calls': [{'index': 0, 'id': 'fixture-save', 'type': 'function',
                                        'function': {'name': 'doc_save', 'arguments': '{}'}}]}
                finish = 'tool_calls'
            else:
                delta = {'content': 'Fixture: read-only recovery review; no CAD edited.'}
                finish = 'stop'
            chunk = {'id': 'fixture', 'object': 'chat.completion.chunk', 'created': 0, 'model': 'fixture',
                     'choices': [{'index': 0, 'delta': delta, 'finish_reason': finish}]}
            content = ('data: ' + json.dumps(chunk) + '\n\ndata: [DONE]\n\n').encode()
            self.send_response(200)
            self.send_header('Content-Type', 'text/event-stream')
            self.send_header('Content-Length', str(len(content)))
            self.end_headers()
            self.wfile.write(content)

    server = ThreadingHTTPServer(('127.0.0.1', 0), Model)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        with tempfile.TemporaryDirectory(prefix='workflow-smoke-', dir=ROOT / 'build') as folder:
            with socket.socket() as sock:
                sock.bind(('127.0.0.1', 0))
                port = sock.getsockname()[1]
            token = secrets.token_urlsafe(24)
            env = dict(os.environ, FORMA_DATA_DIR=folder, FORMA_TOKEN=token, FORMA_API_KEY='local-workflow-fixture')
            process = subprocess.Popen([str(ROOT / 'dist' / 'FormaCAD' / 'FormaCAD.exe'), '--headless', '--port', str(port)], env=env)
            try:
                with httpx.Client(base_url=f'http://127.0.0.1:{port}/api', timeout=40,
                                  headers={'Authorization': 'Bearer ' + token}) as client:
                    deadline = time.monotonic() + 35
                    while True:
                        try:
                            health = client.get('/health').json()
                            if not health['executor_starting']:
                                break
                        except httpx.ConnectError:
                            pass
                        if process.poll() is not None or time.monotonic() > deadline:
                            raise RuntimeError('Frozen workflow fixture did not start')
                        time.sleep(.1)
                    assert health['mcp']['tools'] == 161
                    assert not health['solidworks']['connected'], 'Run this smoke only with no attached CAD document'
                    client.put('/settings', json={'base_url': f'http://127.0.0.1:{server.server_port}/v1', 'model': 'fixture'}).raise_for_status()
                    session = client.post('/sessions', json={}).json()
                    records = []
                    with client.stream('POST', '/chat', json={'session_id': session['id'], 'message': '保存 doc_save'}) as response:
                        response.raise_for_status()
                        for line in response.iter_lines():
                            if not line.startswith('data: '):
                                continue
                            event = json.loads(line[6:])
                            records.append(event)
                            if event['type'] == 'approval_required':
                                approval = event['approval']
                                assert approval['name'] == 'doc_save' and approval['arguments'] == {}
                                current = client.get('/health').json()
                                assert current['active_task']['status'] == 'waiting_confirmation'
                                client.post(f"/tasks/{approval['task_id']}/approvals/{approval['id']}", json={'approved': False}).raise_for_status()
                    assert any(e['type'] == 'approval_required' for e in records)
                    assert records[-1]['type'] == 'done' and records[-1]['status'] == 'cancelled'
                    task = records[-1]['task']['id']
                    report = client.get(f'/tasks/{task}/report').json()
                    assert report['checkpoints'] and all(c['name'] == 'sw_status' for c in report['checkpoints'])
                    assert not report['report']['engineering_verified']
                    assert client.get('/health').json()['pending_approval'] is None
                    preview = client.post(f'/tasks/{task}/recovery-check')
                    preview.raise_for_status()
                    assert preview.json()['mode'] == 'read_only_review'
                    result = client.post('/chat', json={'session_id': session['id'], 'message': '只读核对恢复任务', 'recovery_task_id': task})
                    result.raise_for_status()
                    assert 'recovery_review' in result.text and 'verification_report' in result.text
                    assert len(requests) == 2
                    advertised = {t['function']['name'] for t in requests[-1]['tools']}
                    assert 'doc_save' not in advertised and 'doc_new' not in advertised and 'vba_run' not in advertised
                    export = client.get(f"/sessions/{session['id']}/export").json()
                    assert len(export['execution_reports']) == 2
                    client.post('/mcp/disconnect').raise_for_status()
                    print('Frozen one-time confirmation, denied save, checkpoint evidence and read-only recovery passed; zero CAD edits.')
            finally:
                process.terminate()
                process.wait(timeout=15)
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


if __name__ == '__main__':
    main()
