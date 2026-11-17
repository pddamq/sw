"""Long-running tasks retain cancellation, steering and output-limit semantics."""
import asyncio
from types import SimpleNamespace as NS

import pytest

from backend.agent import CadAgent
from backend.config import SettingsStore
from backend.knowledge import KnowledgeBase
from backend.output_limit import OutputLimitReached
from test_agent import FakeBridge, FakeClient, chunk
from test_steering import fixture
from test_streaming_preview import setup


def calls(count):
    return [NS(index=i, id=f'call-{i}', function=NS(name='sw_status', arguments='{}'))
            for i in range(count)]


async def test_single_batch_can_cross_old_total_call_limit(tmp_path, monkeypatch):
    import backend.agent as module
    agent, db, sid, bridge, events, emit = setup(tmp_path, monkeypatch)
    captures = []
    monkeypatch.setattr(module, 'AsyncOpenAI', lambda **kw: FakeClient([
        [chunk(calls=calls(51))], [chunk('All results received.')],
    ], captures))
    await agent.run(sid, 'Inspect status', emit)
    results = [e for e in events if e['type'] == 'tool_result' and e.get('call_id')]
    assert len(results) == 51
    assert len({e['call_id'] for e in results}) == 51
    assert not any(e['is_error'] for e in results)
    assert len(captures) == 2 and captures[1]['tools']
    assert len([m for m in captures[1]['messages'] if m['role'] == 'tool']) == 51


async def test_cancel_during_long_batch_stops_remaining_calls(tmp_path, monkeypatch):
    import backend.agent as module
    agent, db, sid, bridge, events, original_emit = setup(tmp_path, monkeypatch)
    captures = []
    monkeypatch.setattr(module, 'AsyncOpenAI', lambda **kw: FakeClient([
        [chunk('Partial progress.', calls(60))],
    ], captures))

    async def emit(event):
        await original_emit(event)
        if event['type'] == 'tool_result' and event.get('call_id') == 'call-49':
            asyncio.current_task().cancel()

    task = asyncio.create_task(agent.run(sid, 'Inspect status', emit))
    with pytest.raises(asyncio.CancelledError):
        await task
    results = [e for e in events if e['type'] == 'tool_result' and e.get('call_id')]
    assert len(results) == 50 and len(captures) == 1
    assert not any(e.get('call_id') == 'call-50' for e in events)
    assert db.messages(sid)[-1]['content'] == 'Partial progress.'


@pytest.mark.parametrize('ceiling', [False, True])
async def test_steering_remains_open_after_old_round_ceiling(tmp_path, monkeypatch, ceiling):
    import backend.agent as module
    db, sid, task, store, inbox = fixture(tmp_path)
    settings = SettingsStore(tmp_path / 'settings.json')
    monkeypatch.setenv('FORMA_API_KEY', 'fixture')
    captures, events = [], []
    responses = [[chunk(calls=[NS(index=0, id=f'prior-{i}',
                     function=NS(name='sw_status', arguments='{}'))])] for i in range(20)]
    if ceiling:
        limited = chunk('Incomplete')
        limited.choices[0].finish_reason = 'length'
        responses.append([limited])
    responses.append([chunk('Follow-up completed.')])
    monkeypatch.setattr(module, 'AsyncOpenAI', lambda **kw: FakeClient(responses, captures))
    bridge = FakeBridge()
    agent = CadAgent(db, settings, bridge, KnowledgeBase(db, settings))
    agent.inbox = inbox
    agent.control = NS(task_id=task['id'], permits=lambda name: True, recovery_context=None,
                       confirmation_reason=lambda name, args: None,
                       invoke=lambda name, args, purpose=None: bridge.call(name, args))

    async def emit(event):
        events.append(event)
        trigger = (event['type'] == 'output_limit' if ceiling else
                   event['type'] == 'tool_result' and event.get('call_id') == 'prior-19')
        if trigger:
            assert inbox.open
            inbox.accept('late-input', 'Use the new requirement')

    if ceiling:
        with pytest.raises(OutputLimitReached):
            await agent.run(sid, 'Inspect', emit)
    else:
        await agent.run(sid, 'Inspect', emit)
    assert len(captures) == (22 if ceiling else 21)
    assert store.entries(sid)[0]['status'] == 'consumed'
    assert not inbox.open
    assert captures[-1]['messages'][-1]['content'] == 'Use the new requirement'
    assert captures[-1]['tools']
    assert sum(e['type'] == 'tool_result' and bool(e.get('call_id')) for e in events) == 20
    assert sum(e['type'] == 'steering_consumed' for e in events) == 1
    assert db.messages(sid)[-1]['content'] == 'Follow-up completed.'
