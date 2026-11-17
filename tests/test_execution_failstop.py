import asyncio
import json
from types import SimpleNamespace as NS

import httpx
import pytest

from backend.agent import CadAgent
from backend.app import create_app
from backend.config import SettingsStore
from backend.execution import DecisionStopped, ExecutionHalted, bounded_result, build_report
from backend.knowledge import KnowledgeBase
from test_agent import FakeClient, chunk
from test_execution_control import environment, wait_pending


@pytest.mark.parametrize('failure', ['timeout', 'disconnect', 'invalid_result'])
async def test_transport_failures_leave_unknown_checkpoint(environment, monkeypatch, failure):
    db, _, run, bridge, _, control = environment
    async def broken(name, args):
        if failure == 'invalid_result':
            return {'content': 'incomplete envelope'}
        if failure == 'timeout':
            raise TimeoutError('fixture')
        raise RuntimeError('disconnected')
    monkeypatch.setattr(bridge, 'call', broken)
    with pytest.raises(ExecutionHalted):
        await control.invoke('model_measure', {})
    assert db.checkpoints(run['id'])[0]['status'] == 'unknown'
    assert build_report(db, run['id'])['uncertain_count'] == 1


async def test_approval_snapshot_is_not_a_live_reference(environment, monkeypatch):
    _, _, _, bridge, _, control = environment
    async def shared(name, args):
        bridge.calls.append(name)
        return {'content': json.dumps(bridge.sw_status), 'data': bridge.sw_status, 'is_error': False}
    monkeypatch.setattr(bridge, 'call', shared)
    task = asyncio.create_task(control.invoke('doc_save', {}))
    approval = await wait_pending(control)
    bridge.sw_status['documents'][0]['path'] = 'C:/different.sldprt'
    assert approval['document_state']['documents'][0]['path'] == 'C:/parts/plate.sldprt'
    control.decide(approval['id'], True)
    with pytest.raises(DecisionStopped, match='变化'):
        await task
    assert 'doc_save' not in bridge.calls


async def test_expired_decision_rejected_before_timeout_callback_runs(environment):
    _, _, _, bridge, _, control = environment
    task = asyncio.create_task(control.invoke('doc_save', {}))
    approval = await wait_pending(control)
    control.deadline = asyncio.get_running_loop().time() - 1
    with pytest.raises(ValueError):
        control.decide(approval['id'], True)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert control.deadline is None and control.pending is None
    assert all(name == 'sw_status' for name, _ in bridge.calls)


async def test_status_transport_failure_after_approval_stops_before_save(environment, monkeypatch):
    db, _, run, bridge, events, control = environment
    task = asyncio.create_task(control.invoke('doc_save', {}))
    approval = await wait_pending(control)
    async def disconnected(name, args):
        raise RuntimeError('lost connection')
    monkeypatch.setattr(bridge, 'call', disconnected)
    control.decide(approval['id'], True)
    with pytest.raises(DecisionStopped):
        await task
    assert not db.checkpoints(run['id'])
    assert control.pending is None
    assert events[-1]['type'] == 'approval_resolved'


@pytest.mark.parametrize('cancel', [False, True])
async def test_postcheck_failure_preserves_return_and_stops(environment, monkeypatch, cancel):
    db, _, run, bridge, _, control = environment
    original = bridge.call
    returned = asyncio.Event()
    async def postcheck(name, args):
        if name == 'sw_status' and returned.is_set():
            if cancel:
                raise asyncio.CancelledError()
            raise RuntimeError('status unavailable after committed save')
        result = await original(name, args)
        if name == 'doc_save':
            returned.set()
        return result
    monkeypatch.setattr(bridge, 'call', postcheck)
    task = asyncio.create_task(control.invoke('doc_save', {}))
    approval = await wait_pending(control)
    control.decide(approval['id'], True)
    with pytest.raises(asyncio.CancelledError if cancel else ExecutionHalted):
        await task
    row = db.checkpoints(run['id'])[0]
    assert row['status'] == 'returned' and row['warning']
    assert row['result']['data']['savedTo'] == 'C:/parts/plate.sldprt'
    assert build_report(db, run['id'])['uncertain_count'] == 0


@pytest.mark.parametrize('phase', ['initial', 'operation', 'final'])
async def test_real_graph_does_not_replan_after_uncertain_result(environment, tmp_path, monkeypatch, phase):
    import backend.agent as module
    db, session, _, bridge, events, control = environment
    monkeypatch.setenv('FORMA_API_KEY', 'fixture-key')
    config = SettingsStore(tmp_path / 'settings.json')
    captures = []
    calls = [NS(index=i, id=f'call-{i}', function=NS(name='model_measure', arguments='{}')) for i in range(2)]
    responses = [[chunk('Inspecting fixture.', calls if phase == 'operation' else [])]]
    monkeypatch.setattr(module, 'AsyncOpenAI', lambda **kw: FakeClient(responses, captures))
    original = bridge.call
    observed = []
    async def broken(name, args):
        observed.append(name)
        if (phase == 'initial' or (phase == 'operation' and name == 'model_measure')
                or (phase == 'final' and len(observed) == 2)):
            raise RuntimeError('fixture disconnected')
        return await original(name, args)
    monkeypatch.setattr(bridge, 'call', broken)
    async def emit(event):
        events.append(event)
    agent = CadAgent(db, config, bridge, KnowledgeBase(db, config))
    agent.control = control
    with pytest.raises(ExecutionHalted):
        await agent.run(session['id'], 'model_measure', emit)
    assert len(captures) == (0 if phase == 'initial' else 1)
    assert observed.count('model_measure') == (1 if phase == 'operation' else 0)


async def test_api_records_failed_not_completed_for_unknown_operation(environment, tmp_path, monkeypatch):
    db, session, _, bridge, _, _ = environment
    monkeypatch.setenv('FORMA_API_KEY', 'fixture-key')
    app = create_app(db, SettingsStore(tmp_path / 'settings.json'), bridge, token='fixture')
    async def broken(name, args):
        raise TimeoutError('fixture')
    monkeypatch.setattr(bridge, 'call', broken)
    async def run(session_id, query, emit):
        await app.state.agent.control.invoke('model_measure', {})
    monkeypatch.setattr(app.state.agent, 'run', run)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url='http://testserver',
                                headers={'Authorization': 'Bearer fixture'}) as api:
        response = await api.post('/api/chat', json={'session_id': session['id'], 'message': 'measure'})
        events = [json.loads(line[6:]) for line in response.text.splitlines() if line.startswith('data: ')]
        assert events[-1]['status'] == 'failed'
        task_id = events[-1]['task']['id']
        report = (await api.get(f'/api/tasks/{task_id}/report')).json()['report']
        assert report['uncertain_count'] == 1 and report['returned_count'] == 0
        assert not (await api.get('/api/health')).json()['task_running']


def test_bounded_evidence_marks_text_and_data_truncation():
    result = bounded_result({'content': 'x' * 18001, 'data': {'value': 'x' * 24000}, 'is_error': False})
    assert result['content_truncated'] and result['data_truncated']
    assert len(result['content']) == 18000 and result['data'] == {}
    result = bounded_result({'content': 'short', 'data': {'value': 42}, 'is_error': False})
    assert not result['content_truncated'] and not result['data_truncated']
