import asyncio
from types import SimpleNamespace as NS

import pytest

from backend.agent import CadAgent
from backend.config import SettingsStore
from backend.conversation_journal import ConversationJournal
from backend.knowledge import KnowledgeBase
from backend.steering import SteeringInbox, SteeringStore
from backend.storage import Database
from test_agent import FakeBridge, FakeClient, chunk


def fixture(tmp_path):
    db = Database(tmp_path / 'steering.db')
    sid = db.create_session()['id']
    task = db.create_run(sid, 'Inspect')
    store = SteeringStore(db)
    return db, sid, task, store, SteeringInbox(store, sid, task['id'])


def test_durable_receipt_idempotency_and_atomic_consumption(tmp_path):
    db, sid, task, store, inbox = fixture(tmp_path)
    inbox.accept('q1', 'Change requirement')
    inbox.accept('q1', 'Change requirement')
    assert len(store.entries(sid)) == 1
    assert len(db.messages(sid)) == 1
    with pytest.raises(ValueError):
        inbox.accept('q1', 'Different')
    claimed = inbox.claim()
    assert len(claimed) == 1
    assert db.messages(sid)[-1]['id'] == claimed[0]['id']
    assert inbox.claim() == []
    inbox.close()
    assert inbox.accept('q1', 'Change requirement')['status'] == 'consumed'
    with pytest.raises(ValueError):
        inbox.accept('q2', 'Late')


def test_stopped_or_restarted_pending_inputs_are_released_not_replayed(tmp_path):
    db, sid, task, store, inbox = fixture(tmp_path)
    inbox.accept('q1', 'Pending')
    db.update_run(task['id'], 'interrupted')
    reopened = SteeringStore(db)
    assert reopened.entries(sid)[0]['status'] == 'released'
    assert len(db.messages(sid)) == 1
    inbox.release()
    assert store.entries(sid)[0]['status'] == 'released'


@pytest.mark.parametrize('during_tools', [False, True])
async def test_steering_consumed_after_complete_step_even_after_final_reply(tmp_path, monkeypatch, during_tools):
    import backend.agent as module
    db, sid, task, store, inbox = fixture(tmp_path)
    settings = SettingsStore(tmp_path / 'settings.json')
    monkeypatch.setenv('FORMA_API_KEY', 'fixture')
    captures, events = [], []
    call = NS(index=0, id='c1', function=NS(name='sw_status', arguments='{}'))
    replies = [[chunk('Original answer', [call] if during_tools else [])], [chunk('Revised answer')]]
    monkeypatch.setattr(module, 'AsyncOpenAI', lambda **kw: FakeClient(replies, captures))
    bridge = FakeBridge()
    agent = CadAgent(db, settings, bridge, KnowledgeBase(db, settings))
    agent.inbox = inbox
    # The real control supplies this stable journal task identity.
    agent.control = NS(task_id=task['id'], permits=lambda name: True, recovery_context=None,
                       confirmation_reason=lambda name, args: None,
                       invoke=lambda name, args, purpose=None: bridge.call(name, args))
    async def emit(event):
        events.append(event)
        if (event['type'] == ('tool_result' if during_tools else 'answer') and event.get('round') == 1
                and (not during_tools or event.get('call_id') == 'c1')):
            inbox.accept('q1', 'Use the new requirement')
    await agent.run(sid, 'Inspect', emit)
    assert len(captures) == 2
    outgoing = captures[1]['messages']
    user = next(i for i, row in enumerate(outgoing) if row.get('content') == 'Use the new requirement')
    if during_tools:
        assert outgoing[user - 1]['role'] == 'tool'
    else:
        assert outgoing[user - 1]['role'] == 'assistant'
    assert not inbox.open
    assert store.entries(sid)[0]['status'] == 'consumed'
    assert len([e for e in events if e['type'] == 'steering_consumed']) == 1
    assert [m['content'] for m in db.messages(sid)] == ['Inspect', 'Original answer', 'Use the new requirement', 'Revised answer']
    restored = ConversationJournal(db).restore(sid, db.messages(sid), 0, settings.value.protocol, settings.value.model, settings.value.base_url)
    assert sum(m.get('content') == 'Original answer' for m in restored) == 1
    assert sum(m.get('content') == 'Revised answer' for m in restored) == 1


@pytest.mark.parametrize('failed', [False, True])
async def test_limit_consumes_pending_steering_but_keeps_ceiling_outcome(tmp_path, monkeypatch, failed):
    import backend.agent as module
    db, sid, task, store, inbox = fixture(tmp_path)
    settings = SettingsStore(tmp_path / 'settings.json')
    monkeypatch.setenv('FORMA_API_KEY', 'fixture')
    broken = chunk('Incomplete')
    from backend.output_limit import OutputLimitReached
    broken.choices[0].finish_reason = 'content_filter' if failed else 'length'
    captures = []
    monkeypatch.setattr(module, 'AsyncOpenAI', lambda **kw: FakeClient([[broken], [chunk('Follow-up completed')]], captures))
    bridge = FakeBridge()
    bridge.running = False
    agent = CadAgent(db, settings, bridge, KnowledgeBase(db, settings))
    agent.inbox = inbox
    async def emit(event):
        if event['type'] == 'reasoning_start':
            inbox.accept('q1', 'Still pending')
    with pytest.raises(RuntimeError if failed else OutputLimitReached):
        await agent.run(sid, 'Inspect', emit)
    inbox.release()
    assert store.entries(sid)[0]['status'] == ('released' if failed else 'consumed')
    assert len(captures) == (1 if failed else 2)
    assert [m['content'] for m in db.messages(sid)] == (
        ['Inspect'] if failed else ['Inspect', 'Still pending', 'Follow-up completed'])
    if not failed:
        assert 'Incomplete' not in str(captures[1]['messages'])
        assert captures[1]['messages'][-1]['content'] == 'Still pending'
