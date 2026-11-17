"""Diagnostics must be useful, bounded, private and independent of execution."""
import json
from types import SimpleNamespace as NS

import httpx
import pytest

from backend.app import create_app
from backend.agent import CadAgent
from backend.config import SettingsStore
from backend.diagnostics import DiagnosticRecorder, export_diagnostics, sanitize
from backend.knowledge import KnowledgeBase
from backend.storage import Database
from test_agent import FakeBridge, FakeClient, chunk


def test_redaction_covers_credentials_nested_json_urls_and_images():
    original = {
        'api_key': 'custom-api-secret',
        'nested': {'Authorization': 'Basic abc123', 'Cookie': 'session=secret-cookie'},
        'arguments': json.dumps({'password': 'hidden-password', 'depth': 15, 'enabled': False}),
        'text': 'known-secret sk-example-secret Bearer bearer-secret password=inline-secret',
        'url': 'https://user:pass@example.test/v1?token=url-secret#fragment',
        'image_url': {'url': 'data:image/png;base64,QUJDREVGRw=='},
        'images': [{'mimeType': 'image/png', 'data': 'RAWIMAGEPAYLOAD'}],
    }
    clean = sanitize(original, ('known-secret',))
    serialized = json.dumps(clean)
    for secret in ['custom-api-secret', 'abc123', 'secret-cookie', 'hidden-password',
                   'known-secret', 'sk-example-secret', 'bearer-secret', 'inline-secret',
                   'url-secret', 'user:pass', 'QUJDREVGRw==', 'RAWIMAGEPAYLOAD']:
        assert secret not in serialized
    assert json.loads(clean['arguments']) == {'password': '[redacted]', 'depth': 15, 'enabled': False}
    assert original['api_key'] == 'custom-api-secret'


def test_records_are_bounded_isolated_and_deleted_with_session(tmp_path, monkeypatch):
    import backend.diagnostics as module
    monkeypatch.setattr(module, 'MAX_SESSION_RECORDS', 3)
    monkeypatch.setattr(module, 'MAX_TOTAL_RECORDS', 4)
    db = Database(tmp_path / 'log.db')
    first, second = db.create_session(), db.create_session()
    recorder = DiagnosticRecorder(db, first['id'], secrets=('secret-value',))
    for index in range(5):
        recorder('fixture', {'index': index, 'content': 'secret-value'})
    assert [r['data']['index'] for r in export_diagnostics(db, first['id'])['records']] == [2, 3, 4]
    other = DiagnosticRecorder(db, second['id'])
    other('other', {'value': 1})
    other('other', {'value': 2})
    assert len(export_diagnostics(db, first['id'])['records']) == 2
    assert db.events(first['id']) == []
    assert db.messages(first['id']) == []
    with db.connect() as conn:
        payloads = ''.join(r['payload'] for r in conn.execute('SELECT payload FROM diagnostic_records'))
    assert 'secret-value' not in payloads
    db.delete_session(first['id'])
    assert export_diagnostics(db, first['id'])['records'] == []
    assert len(export_diagnostics(db, second['id'])['records']) == 2


def test_oversize_records_have_explicit_truncation(tmp_path):
    from backend.diagnostics import MAX_RECORD_BYTES
    db = Database(tmp_path / 'log.db')
    sid = db.create_session()['id']
    recorder = DiagnosticRecorder(db, sid)
    recorder('large', {'items': ['a' * 16001] * 20})
    row = export_diagnostics(db, sid)['records'][0]
    assert row['data']['truncated'] is True
    with db.connect() as conn:
        payload = conn.execute('SELECT payload FROM diagnostic_records').fetchone()['payload']
    assert len(payload.encode('utf-8')) <= MAX_RECORD_BYTES


def test_oversize_request_keeps_redacted_routing_evidence(tmp_path):
    from backend.diagnostics import MAX_RECORD_BYTES
    db = Database(tmp_path / 'snapshot.db')
    sid = db.create_session()['id']
    recorder = DiagnosticRecorder(db, sid, secrets=('fixture-secret',))
    request = {'model': 'fixture-model', 'messages': [
        {'role': 'user', 'content': 'fixture-secret ' + '\u8349' * 15000} for _ in range(20)],
        'tools': [{'type': 'function', 'function': {'name': name, 'parameters': {}}}
                  for name in ('doc_activate', 'sketch_info', 'request_clarification')],
        'tool_choice': 'required', 'max_tokens': 8192, 'temperature': 0.2, 'stream': True}
    recorder('model_request', {'round': 5, 'attempt': 2,
        'base_url': 'https://user:fixture-secret@example.test/v1?token=fixture-secret', 'request': request})
    data = export_diagnostics(db, sid)['records'][0]['data']
    assert data['truncated']
    assert data['round'] == 5 and data['attempt'] == 2
    snapshot = data['request_snapshot']
    assert snapshot['model'] == 'fixture-model'
    assert snapshot['tool_choice'] == 'required'
    assert snapshot['max_tokens'] == 8192
    assert snapshot['message_count'] == 20
    assert snapshot['tool_count'] == 3
    assert snapshot['tool_names'] == ['doc_activate', 'sketch_info', 'request_clarification']
    assert not snapshot['tool_names_truncated']
    assert 'fixture-secret' not in json.dumps(data)
    assert 'user:' not in snapshot['base_url']
    assert len(json.dumps(data, ensure_ascii=False).encode('utf-8')) <= MAX_RECORD_BYTES
    assert request['messages'][0]['content'].startswith('fixture-secret')


def test_request_snapshot_forced_choice_and_extreme_names_remain_bounded(tmp_path):
    from backend.diagnostics import MAX_RECORD_BYTES
    db = Database(tmp_path / 'snapshot.db')
    sid = db.create_session()['id']
    recorder = DiagnosticRecorder(db, sid)
    recorder('model_request', {'round': 1, 'attempt': 1, 'request': {
        'messages': [{'role': 'user', 'content': 'x' * 16000}] * 20,
        'tool_choice': {'type': 'function', 'function': {'name': 'sketch_info'}},
        'tools': [{'function': {'name': '\u0001' * 128}}] * 201}})
    data = export_diagnostics(db, sid)['records'][0]['data']
    snapshot = data['request_snapshot']
    assert snapshot['tool_choice']['function']['name'] == 'sketch_info'
    assert snapshot['tool_count'] == 201
    assert snapshot['tool_names_truncated']
    assert len(snapshot['tool_names']) < 200
    assert len(json.dumps(data, ensure_ascii=False).encode('utf-8')) <= MAX_RECORD_BYTES
    assert not recorder.failed


def test_diagnostic_storage_failure_does_not_abort_execution(tmp_path, monkeypatch):
    db = Database(tmp_path / 'log.db')
    recorder = DiagnosticRecorder(db, db.create_session()['id'])
    def broken():
        raise OSError('sensitive storage error')
    monkeypatch.setattr(db, 'connect', broken)
    recorder('event', {'value': 1})
    recorder('event', {'value': 2})
    assert recorder.failed


async def test_rejected_model_response_and_correction_are_logged_before_execution(tmp_path, monkeypatch):
    import backend.agent as module
    monkeypatch.setenv('FORMA_API_KEY', 'fixture-secret')
    db = Database(tmp_path / 'log.db')
    settings = SettingsStore(tmp_path / 'settings.json')
    sid = db.create_session()['id']
    db.add_message(sid, 'user', 'Inspect')
    captures = []
    call = NS(index=0, id='corrected', function=NS(name='sw_status', arguments='{}'))
    monkeypatch.setattr(module, 'AsyncOpenAI', lambda **kw: FakeClient([
        [chunk('<tool_call>fixture-secret</tool_call>')], [chunk(calls=[call])], [chunk('Done')]
    ], captures))
    bridge = FakeBridge()
    agent = CadAgent(db, settings, bridge, KnowledgeBase(db, settings))
    agent.diagnostic = DiagnosticRecorder(db, sid, secrets=('fixture-secret',))
    async def emit(event):
        pass
    await agent.run(sid, 'Inspect', emit)
    records = export_diagnostics(db, sid)['records']
    responses = [r['data'] for r in records if r['kind'] == 'model_response']
    assert responses[0]['content'] == '<tool_call>[redacted]</tool_call>'
    assert responses[1]['tool_calls'][0]['id'] == 'corrected'
    assert len([r for r in records if r['kind'] == 'model_request']) == 3
    assert len([r for r in records if r['kind'] == 'executor_response']) == 3
    validation = [r['data'] for r in records if r['kind'] == 'protocol_validation']
    assert [r['accepted'] for r in validation] == [False, True, True]
    assert 'textual_tool_protocol' in validation[0]['error']
    assert all(r['created_at'] for r in records)
    assert 'fixture-secret' not in json.dumps(records)
    assert 'diagnostic_records' not in json.dumps(captures)
    assert db.messages(sid)[-1]['content'] == 'Done'


async def test_transport_retry_keeps_discarded_response_without_replaying_tools(tmp_path, monkeypatch):
    import backend.agent as module
    import backend.model_retry as retries
    from test_agent import FakeStream
    monkeypatch.setenv('FORMA_API_KEY', 'fixture-secret')
    async def no_wait(delay):
        pass
    monkeypatch.setattr(retries, 'wait_before_retry', no_wait)
    db = Database(tmp_path / 'retry-log.db')
    settings = SettingsStore(tmp_path / 'settings.json')
    sid = db.create_session()['id']
    db.add_message(sid, 'user', 'Inspect')
    class BrokenStream(FakeStream):
        async def generate(self):
            yield chunk('Partial fixture-secret')
            raise httpx.ReadError('Disconnected')
    class Client(FakeClient):
        async def create(self, **kwargs):
            if not self.captures:
                self.captures.append(kwargs)
                return BrokenStream([])
            return await super().create(**kwargs)
    captures = []
    monkeypatch.setattr(module, 'AsyncOpenAI', lambda **kw: Client([[chunk('Recovered')]], captures))
    bridge = FakeBridge()
    agent = CadAgent(db, settings, bridge, KnowledgeBase(db, settings))
    agent.diagnostic = DiagnosticRecorder(db, sid, secrets=('fixture-secret',))
    async def emit(event):
        pass
    await agent.run(sid, 'Inspect', emit)
    records = export_diagnostics(db, sid)['records']
    requests = [r['data'] for r in records if r['kind'] == 'model_request']
    responses = [r['data'] for r in records if r['kind'] == 'model_response']
    assert [r['attempt'] for r in requests] == [1, 2]
    assert responses[0]['outcome'] == 'discarded_before_transport_retry'
    assert responses[0]['content'] == 'Partial [redacted]'
    assert responses[1]['outcome'] == 'complete'
    assert responses[1]['content'] == 'Recovered'
    assert db.messages(sid)[-1]['content'] == 'Recovered'
    assert bridge.calls == [('sw_status', {})] * 2


async def test_diagnostic_endpoint_authenticated_and_persistent(tmp_path, monkeypatch):
    monkeypatch.setenv('FORMA_API_KEY', 'fixture-secret')
    db = Database(tmp_path / 'log.db')
    settings = SettingsStore(tmp_path / 'settings.json')
    app = create_app(db, settings, bridge=FakeBridge(), token='local-secret')
    async def failing(session_id, query, emit):
        app.state.agent.diagnostic('model_response', {'content': 'fixture-secret local-secret'})
        await emit({'type': 'tool_result', 'name': 'sw_status', 'content': 'fixture-secret', 'is_error': True})
        raise RuntimeError('fixture-secret failure')
    monkeypatch.setattr(app.state.agent, 'run', failing)
    sid = db.create_session()['id']
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url='http://testserver') as api:
        endpoint = f'/api/sessions/{sid}/diagnostics'
        assert (await api.get(endpoint)).status_code == 401
        api.headers['Authorization'] = 'Bearer local-secret'
        await api.post('/api/chat', json={'session_id': sid, 'message': 'Inspect'})
        response = await api.get(endpoint)
        assert response.status_code == 200
        assert 'attachment' in response.headers['content-disposition']
        data = response.json()
        assert data['app_version']
        assert {r['kind'] for r in data['records']} >= {'task_start', 'model_response', 'event', 'task_exception'}
        assert all(r['task_id'] == db.runs(sid)[0]['id'] for r in data['records'])
        assert 'fixture-secret' not in response.text
        assert 'local-secret' not in response.text
        assert 'RuntimeError' in response.text
        assert app.state.agent.diagnostic is None
        assert (await api.get('/api/sessions/missing/diagnostics')).status_code == 404
    reopened = Database(tmp_path / 'log.db')
    assert export_diagnostics(reopened, sid)['records'] == data['records']
