from types import SimpleNamespace as NS

import pytest

from backend.agent import CadAgent
from backend.config import SettingsStore
from backend.knowledge import KnowledgeBase
from backend.storage import Database
from test_agent import FakeBridge, FakeClient, chunk


@pytest.mark.parametrize('field', ['reasoning_content', 'reasoning'])
@pytest.mark.parametrize('effort', [None, 'low', 'medium', 'high'])
async def test_reasoning_stream_separate_from_answer_and_retained_for_tool_round(tmp_path, monkeypatch, field, effort):
    import backend.agent as module
    monkeypatch.setenv('FORMA_API_KEY', 'fixture-key')
    db = Database(tmp_path / 'reasoning.db')
    config = SettingsStore(tmp_path / 'settings.json')
    first, second = chunk(), chunk()
    setattr(first.choices[0].delta, field, 'Inspect ')
    setattr(second.choices[0].delta, field, 'the connection.')
    call = NS(index=0, id='c1', function=NS(name='sw_status', arguments='{}'))
    captures = []
    monkeypatch.setattr(module, 'AsyncOpenAI', lambda **kw: FakeClient([
        [first, second, chunk(calls=[call])], [chunk('Connection checked.')],
    ], captures))
    session = db.create_session()
    db.add_message(session['id'], 'user', 'Check connection')
    events = []
    async def emit(event):
        events.append(event)
    agent = CadAgent(db, config, FakeBridge(), KnowledgeBase(db, config))
    agent.reasoning_effort = effort
    await agent.run(session['id'], 'Check connection', emit)
    for request in captures:
        if effort is None:
            assert 'reasoning_effort' not in request
        else:
            assert request['reasoning_effort'] == effort
    end_index = next(i for i, e in enumerate(events) if e['type'] == 'reasoning_end' and e['round'] == 2)
    token_index = next(i for i, e in enumerate(events) if e['type'] == 'token' and e['content'] == 'Connection checked.')
    assert end_index < token_index
    deltas = [e for e in events if e['type'] == 'reasoning_delta']
    assert ''.join(e['content'] for e in deltas) == 'Inspect the connection.'
    assert all(e.get('attempt_id') for e in deltas)
    ends = [e for e in events if e['type'] == 'reasoning_end']
    assert all(e.get('attempt_id') for e in ends)
    assert len({e['attempt_id'] for e in ends}) == 2
    ends = [{k: v for k, v in e.items() if k != 'attempt_id'} for e in ends]
    assert ends == [
        {'type': 'reasoning_end', 'round': 1, 'content': 'Inspect the connection.', 'status': 'completed'},
        {'type': 'reasoning_end', 'round': 2, 'content': '', 'status': 'completed'},
    ]
    assert db.messages(session['id'])[-1]['content'] == 'Connection checked.'
    previous = next(m for m in captures[1]['messages'] if m['role'] == 'assistant')
    assert previous[field] == 'Inspect the connection.'


async def test_interrupted_reasoning_keeps_preview_out_of_model_history(tmp_path, monkeypatch):
    import backend.agent as module
    monkeypatch.setenv('FORMA_API_KEY', 'fixture-key')
    db = Database(tmp_path / 'reasoning.db')
    config = SettingsStore(tmp_path / 'settings.json')
    partial = chunk()
    partial.choices[0].delta.reasoning_content = 'Partial reasoning'
    partial.choices[0].finish_reason = 'length'
    monkeypatch.setattr(module, 'AsyncOpenAI', lambda **kw: FakeClient([[partial]], []))
    session = db.create_session()
    db.add_message(session['id'], 'user', 'Check')
    events = []
    async def emit(event):
        events.append(event)
    with pytest.raises(RuntimeError):
        await CadAgent(db, config, FakeBridge(), KnowledgeBase(db, config)).run(session['id'], 'Check', emit)
    end = next(e for e in events if e['type'] == 'reasoning_end')
    assert end.get('attempt_id')
    assert {k: v for k, v in end.items() if k != 'attempt_id'} == {'type': 'reasoning_end', 'round': 1, 'content': 'Partial reasoning', 'status': 'max_tokens'}
    assert next(e for e in reversed(events) if e['type'] == 'model_attempt')['phase'] == 'max_tokens'
    assert len(db.messages(session['id'])) == 1
