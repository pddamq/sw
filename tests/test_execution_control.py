import asyncio
import copy
import json

import httpx
import pytest

from backend.app import create_app
from backend.config import SettingsStore
from backend.execution import DecisionStopped, ExecutionControl, build_report
from backend.storage import Database


class Bridge:
    running = True
    detail = 'fixture only'
    tools = [
        {'name': 'sw_status', 'annotations': {'readOnlyHint': True}},
        {'name': 'doc_save'}, {'name': 'feature_delete'},
        {'name': 'model_measure', 'annotations': {'readOnlyHint': True}},
        {'name': 'vba_run', 'annotations': {'readOnlyHint': True}},
    ]

    def __init__(self):
        self.calls = []
        self.sw_status = {'attached': True, 'solidWorksPid': 123, 'activeDocument': 'plate',
                          'targetDocument': 'plate', 'documents': [{'handle': 'D1', 'path': 'C:/parts/plate.sldprt',
                          'title': 'plate', 'type': 'part', 'target': True, 'configuration': 'Default'}]}
        self.fail = False

    async def call(self, name, args):
        self.calls.append((name, copy.deepcopy(args)))
        value = copy.deepcopy(self.sw_status) if name == 'sw_status' else {'success': not self.fail, 'savedTo': 'C:/parts/plate.sldprt'}
        return {'content': json.dumps(value), 'data': value, 'is_error': self.fail if name != 'sw_status' else False}


@pytest.fixture
def environment(tmp_path):
    db = Database(tmp_path / 'control.db')
    session = db.create_session()
    run = db.create_run(session['id'], 'fixture')
    bridge = Bridge()
    events = []
    async def emit(event):
        events.append(event)
    return db, session, run, bridge, events, ExecutionControl(db, bridge, run['id'], emit)


async def wait_pending(control):
    for _ in range(100):
        if control.pending:
            return control.pending
        await asyncio.sleep(.001)
    raise AssertionError('Approval did not become pending')


async def test_confirmation_does_not_execute_until_single_exact_approval(environment):
    db, _, run, bridge, events, control = environment
    task = asyncio.create_task(control.invoke('doc_save', {'path': 'C:/parts/plate.sldprt'}))
    approval = await wait_pending(control)
    assert db.run(run['id'])['status'] == 'waiting_confirmation'
    assert all(name == 'sw_status' for name, _ in bridge.calls)
    with pytest.raises(ValueError):
        control.decide('wrong-id', True)
    control.decide(approval['id'], True)
    with pytest.raises(ValueError):
        control.decide(approval['id'], True)
    await task
    assert [name for name, _ in bridge.calls].count('doc_save') == 1
    assert not control.pending
    assert db.checkpoints(run['id'])[0]['snapshot']['attached']
    assert events[-1]['type'] == 'checkpoint'


@pytest.mark.parametrize('action', ['deny', 'change', 'cancel', 'timeout'])
async def test_stale_denied_cancelled_expired_approvals_never_execute(environment, action):
    db, _, run, bridge, events, control = environment
    control.approval_timeout = .02 if action == 'timeout' else 300
    task = asyncio.create_task(control.invoke('doc_save', {}))
    approval = await wait_pending(control)
    if action == 'deny':
        control.decide(approval['id'], False)
    elif action == 'change':
        bridge.sw_status['targetDocument'] = 'other'
        control.decide(approval['id'], True)
    elif action == 'cancel':
        task.cancel()
    with pytest.raises((DecisionStopped, asyncio.CancelledError)):
        await task
    assert all(name == 'sw_status' for name, _ in bridge.calls)
    assert db.checkpoints(run['id']) == []
    assert control.pending is None
    assert events[-1]['type'] == 'approval_resolved'
    with pytest.raises(ValueError):
        control.decide(approval['id'], True)


async def test_unknown_operation_never_claims_rollback_or_success(environment, monkeypatch):
    db, _, run, bridge, _, control = environment
    started = asyncio.Event()
    async def hanging(name, args):
        started.set()
        await asyncio.Event().wait()
    monkeypatch.setattr(bridge, 'call', hanging)
    task = asyncio.create_task(control.invoke('model_measure', {}))
    await started.wait()
    assert db.checkpoints(run['id'])[0]['status'] == 'started'
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    report = build_report(db, run['id'])
    assert report['uncertain_count'] == 1
    assert not report['engineering_verified']
    assert db.checkpoints(run['id'])[0]['status'] == 'unknown'


async def test_report_only_uses_returned_evidence_not_requested_paths(environment):
    db, _, run, bridge, _, control = environment
    bridge.fail = True
    task = asyncio.create_task(control.invoke('doc_save', {'path': 'C:/requested.sldprt'}))
    approval = await wait_pending(control)
    control.decide(approval['id'], True)
    await task
    report = build_report(db, run['id'])
    assert report['error_count'] == 1 and report['returned_count'] == 0
    assert report['engineering_verified'] is False
    assert 'C:/requested.sldprt' not in json.dumps(report)
    assert report['evidence']['saved_file'][0]['status'] == 'error'


async def test_readonly_recovery_ignores_misleading_annotations(environment):
    db, _, run, bridge, events, control = environment
    checkpoint = db.start_checkpoint(run['id'], 'doc_save', {})
    db.finish_checkpoint(checkpoint, 'returned', {}, copy.deepcopy(bridge.sw_status))
    context = await control.prepare_recovery(run['id'])
    assert context['comparison'] == 'same_saved_document'
    assert control.permits('model_measure')
    assert not control.permits('vba_run') and not control.permits('doc_save')
    with pytest.raises(DecisionStopped):
        await control.invoke('vba_run', {})
    assert all(name == 'sw_status' for name, _ in bridge.calls)
    bridge.sw_status['documents'][0]['path'] = 'C:/other.sldprt'
    assert (await control.prepare_recovery(run['id']))['comparison'] == 'different_document'
    bridge.sw_status['documents'][0]['path'] = ''
    assert (await control.prepare_recovery(run['id']))['comparison'] == 'unknown'


def test_checkpoint_durability_migration_and_cascade(environment):
    db, session, run, _, _, _ = environment
    checkpoint = db.start_checkpoint(run['id'], 'model_measure', {'kind': 'distance'})
    reopened = Database(db.path)
    assert reopened.checkpoints(run['id'])[0]['id'] == checkpoint
    assert build_report(reopened, run['id'])['uncertain_count'] == 1
    reopened.delete_session(session['id'])
    assert reopened.checkpoints(run['id']) == []


async def test_real_graph_stops_after_declined_call(environment, tmp_path, monkeypatch):
    import backend.agent as module
    from backend.agent import CadAgent
    from backend.knowledge import KnowledgeBase
    from test_agent import FakeClient, chunk
    from types import SimpleNamespace as NS
    db, session, run, bridge, events, control = environment
    monkeypatch.setenv('FORMA_API_KEY', 'fixture-key')
    config = SettingsStore(tmp_path / 'settings.json')
    captures = []
    call = NS(index=0, id='save-1', function=NS(name='doc_save', arguments=json.dumps({'_forma_purpose': '保存当前零件，以保留本次修改。'})))
    monkeypatch.setattr(module, 'AsyncOpenAI', lambda **kw: FakeClient([[chunk('准备保存。', [call])]], captures))
    async def emit(event):
        events.append(event)
        if event['type'] == 'approval_required':
            control.decide(event['approval']['id'], False)
    control.emit = emit
    agent = CadAgent(db, config, bridge, KnowledgeBase(db, config))
    agent.control = control
    with pytest.raises(DecisionStopped):
        await agent.run(session['id'], '保存 doc_save', emit)
    assert len(captures) == 1
    assert all(name == 'sw_status' for name, _ in bridge.calls)
    assert db.messages(session['id'])[-1]['content'] == '准备保存。'
    assert all(row['name'] == 'sw_status' for row in db.checkpoints(run['id']))


async def test_approval_api_auth_task_binding_and_cancel(tmp_path, monkeypatch):
    monkeypatch.setenv('FORMA_API_KEY', 'fixture-key')
    db = Database(tmp_path / 'api.db')
    bridge = Bridge()
    app = create_app(db, SettingsStore(tmp_path / 'settings.json'), bridge, token='fixture')
    async def fake(session_id, query, emit):
        await app.state.agent.control.invoke('doc_save', {})
    monkeypatch.setattr(app.state.agent, 'run', fake)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url='http://testserver',
                                headers={'Authorization': 'Bearer fixture'}) as api:
        session = (await api.post('/api/sessions', json={})).json()
        chat = asyncio.create_task(api.post('/api/chat', json={'session_id': session['id'], 'message': 'save'}))
        for _ in range(100):
            health = (await api.get('/api/health')).json()
            if health['pending_approval']:
                break
            await asyncio.sleep(.001)
        approval = health['pending_approval']
        assert approval and health['task_running']
        path = f"/api/tasks/{approval['task_id']}/approvals/{approval['id']}"
        assert (await api.post(path, json={'approved': True}, headers={'Authorization': ''})).status_code == 401
        assert (await api.post(path, json={'approved': 'false'})).status_code == 422
        assert (await api.post(path.replace(approval['task_id'], 'wrong'), json={'approved': True})).status_code == 409
        assert (await api.put('/api/settings', json={'model': 'changed'})).status_code == 409
        assert (await api.post('/api/tasks/cancel')).json()['requested']
        response = await asyncio.wait_for(chat, 2)
        assert 'verification_report' in response.text
        assert db.run(approval['task_id'])['status'] == 'cancelled'
        assert (await api.post(path, json={'approved': True})).status_code == 409
        assert (await api.get('/api/health')).json()['pending_approval'] is None
        assert all(name == 'sw_status' for name, _ in bridge.calls)


async def test_recovery_api_is_readonly_and_checks_session(tmp_path, monkeypatch):
    monkeypatch.setenv('FORMA_API_KEY', 'fixture-key')
    db = Database(tmp_path / 'recovery.db')
    bridge = Bridge()
    app = create_app(db, SettingsStore(tmp_path / 'settings.json'), bridge, token='fixture')
    session = db.create_session()
    source = db.create_run(session['id'], 'old task')
    db.update_run(source['id'], 'interrupted')
    async def fake(session_id, query, emit):
        assert app.state.agent.control.read_only
        await app.state.agent.control.invoke('doc_save', {})
    monkeypatch.setattr(app.state.agent, 'run', fake)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url='http://testserver',
                                headers={'Authorization': 'Bearer fixture'}) as api:
        result = await api.post(f"/api/tasks/{source['id']}/recovery-check")
        assert result.status_code == 200 and result.json()['mode'] == 'read_only_review'
        other = db.create_session()
        payload = {'session_id': other['id'], 'message': 'review', 'recovery_task_id': source['id']}
        assert (await api.post('/api/chat', json=payload)).status_code == 409
        payload['session_id'] = session['id']
        response = await api.post('/api/chat', json=payload)
        assert 'recovery_review' in response.text
        assert db.runs(session['id'])[0]['status'] == 'cancelled'
        assert all(name == 'sw_status' for name, _ in bridge.calls)
        assert (await api.get(f"/api/tasks/{source['id']}/report")).json()['report']['engineering_verified'] is False
        assert 'execution_reports' in (await api.get(f"/api/sessions/{session['id']}/export")).json()
