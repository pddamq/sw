"""Display previews must arrive before finish without admitting partial operations."""
import asyncio
from types import SimpleNamespace as NS

import pytest

from backend.agent import CadAgent
from backend.config import SettingsStore
from backend.knowledge import KnowledgeBase
from backend.storage import Database
from test_agent import FakeBridge, FakeClient, FakeStream, chunk


def setup(tmp_path, monkeypatch):
    monkeypatch.setenv('FORMA_API_KEY', 'fixture')
    db = Database(tmp_path / 'stream.db')
    settings = SettingsStore(tmp_path / 'settings.json')
    session = db.create_session()['id']
    db.add_message(session, 'user', 'Inspect status')
    bridge = FakeBridge()
    events = []

    async def emit(event):
        events.append(event)

    return CadAgent(db, settings, bridge, KnowledgeBase(db, settings)), db, session, bridge, events, emit


async def test_previews_arrive_before_stream_finishes_and_tools_wait(tmp_path, monkeypatch):
    import backend.agent as module
    agent, db, sid, bridge, events, emit = setup(tmp_path, monkeypatch)
    captures = []

    class LiveStream(FakeStream):
        async def generate(self):
            thought = chunk()
            thought.choices[0].delta.reasoning_content = 'Inspect current state.'
            yield thought
            assert events[-1]['type'] == 'reasoning_delta'
            assert events[-1]['content'] == 'Inspect current state.'
            yield chunk('Checking ')
            assert events[-1]['type'] == 'answer_delta'
            assert events[-1]['content'] == 'Checking '
            yield chunk('status.')
            assert events[-1]['content'] == 'status.'
            call = NS(index=0, id='live', function=NS(name='sw_status', arguments='{}'))
            yield chunk(calls=[call])
            assert bridge.calls == [('sw_status', {})]
            assert len(db.messages(sid)) == 1
            assert not any(e['type'] in {'token', 'answer'} for e in events)
            end = chunk()
            end.choices[0].finish_reason = 'tool_calls'
            yield end

    class Client(FakeClient):
        async def create(self, **kwargs):
            captures.append(kwargs)
            return LiveStream([]) if len(captures) == 1 else FakeStream([chunk('Checked.')])

    monkeypatch.setattr(module, 'AsyncOpenAI', lambda **kw: Client([], captures))
    await agent.run(sid, 'Inspect status', emit)
    assert bridge.calls == [('sw_status', {})] * 3
    assert db.messages(sid)[-1]['content'] == 'Checking status.\n\nChecked.'
    previews = [e for e in events if e['type'] == 'answer_delta']
    assert ''.join(e['content'] for e in previews) == 'Checking status.Checked.'
    assert all(e.get('attempt_id') and e.get('round') for e in previews)
    assert len({e['attempt_id'] for e in previews}) == 2


@pytest.mark.parametrize('cancelled', [False, True])
async def test_interrupted_preview_is_display_only_and_never_executes(tmp_path, monkeypatch, cancelled):
    import backend.agent as module
    agent, db, sid, bridge, events, emit = setup(tmp_path, monkeypatch)

    class Stream(FakeStream):
        async def generate(self):
            call = NS(index=0, id='partial', function=NS(name='sw_status', arguments='{}'))
            yield chunk('Unfinished answer', [call])
            if cancelled:
                raise asyncio.CancelledError()
            end = chunk()
            end.choices[0].finish_reason = 'length'
            yield end

    class Client(FakeClient):
        async def create(self, **kwargs):
            return Stream([])

    monkeypatch.setattr(module, 'AsyncOpenAI', lambda **kw: Client([], []))
    with pytest.raises(asyncio.CancelledError if cancelled else RuntimeError):
        await agent.run(sid, 'Inspect status', emit)
    snapshot = next(e for e in events if e['type'] == 'answer_preview_end')
    assert snapshot['content'] == 'Unfinished answer'
    assert snapshot['status'] == ('interrupted' if cancelled else 'max_tokens')
    assert bridge.calls == [('sw_status', {})]
    assert len(db.messages(sid)) == 1
    assert not any(e['type'] in {'token', 'answer'} for e in events)


async def test_transport_retry_uses_fresh_preview_and_keeps_completed_history(tmp_path, monkeypatch):
    import backend.agent as module
    import backend.model_retry as retry
    agent, db, sid, bridge, events, emit = setup(tmp_path, monkeypatch)
    captures = []

    async def wait(delay):
        pass

    class TruncatedStream(FakeStream):
        async def generate(self):
            yield chunk('Discarded preview')
            # EOF without a finish marker triggers the transport retry.

    class Client(FakeClient):
        async def create(self, **kwargs):
            captures.append(kwargs)
            return TruncatedStream([]) if len(captures) == 1 else FakeStream([chunk('Fresh answer')])

    monkeypatch.setattr(retry, 'wait_before_retry', wait)
    monkeypatch.setattr(module, 'AsyncOpenAI', lambda **kw: Client([], captures))
    await agent.run(sid, 'Inspect status', emit)
    assert captures[0] == captures[1]
    assert db.messages(sid)[-1]['content'] == 'Fresh answer'
    reset = next(e for e in events if e['type'] == 'model_retry')
    assert reset['content'] == ''
    previews = [e for e in events if e['type'] == 'answer_delta']
    assert [e['content'] for e in previews] == ['Discarded preview', 'Fresh answer']
    assert previews[0]['attempt_id'] != previews[1]['attempt_id']


async def test_protocol_rejection_clears_preview_without_committing_it(tmp_path, monkeypatch):
    import backend.agent as module
    agent, db, sid, bridge, events, emit = setup(tmp_path, monkeypatch)
    bad = NS(index=0, id='bad', function=NS(name='sw_status', arguments='{"bogus":true}'))
    good = NS(index=0, id='good', function=NS(name='sw_status', arguments='{}'))
    captures = []
    monkeypatch.setattr(module, 'AsyncOpenAI', lambda **kw: FakeClient([
        [chunk('Invalid plan', [bad])], [chunk(calls=[good])], [chunk('Verified.')],
    ], captures))
    await agent.run(sid, 'Inspect status', emit)
    cleared = next(e for e in events if e['type'] == 'answer_preview_end')
    assert cleared['status'] == 'rejected' and cleared['content'] == ''
    assert 'Invalid plan' not in str(captures[1]['messages'])
    assert db.messages(sid)[-1]['content'] == 'Verified.'
    assert bridge.calls == [('sw_status', {})] * 3
