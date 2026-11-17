"""Regression coverage for consecutive, scoped protocol repairs."""
from types import SimpleNamespace as NS

import pytest

from backend.agent import CadAgent
from backend.config import SettingsStore
from backend.knowledge import KnowledgeBase
from backend.storage import Database
from test_agent import FakeBridge, FakeClient, chunk


async def test_success_resets_repairs_and_drops_stale_correction_instructions(tmp_path, monkeypatch):
    import backend.agent as module
    monkeypatch.setenv('FORMA_API_KEY', 'fixture')
    db = Database(tmp_path / 'repair.db')
    settings = SettingsStore(tmp_path / 'settings.json')
    session = db.create_session()
    db.add_message(session['id'], 'user', 'Inspect status')
    captures, events, responses = [], [], []
    for cycle in range(3):
        responses.extend([[chunk('<tool_call>invalid</tool_call>')]] * 2)
        call = NS(index=0, id=f'valid_{cycle}', function=NS(name='sw_status', arguments='{}'))
        responses.append([chunk(calls=[call])])
    responses.append([chunk('Status verified.')])
    monkeypatch.setattr(module, 'AsyncOpenAI', lambda **kw: FakeClient(responses, captures))
    bridge = FakeBridge()

    async def emit(event):
        events.append(event)

    await CadAgent(db, settings, bridge, KnowledgeBase(db, settings)).run(session['id'], 'Inspect status', emit)
    assert len(captures) == 10
    assert bridge.calls == [('sw_status', {})] * 5
    for index, request in enumerate(captures):
        repairs = [m for m in request['messages']
                   if isinstance(m.get('content'), str) and 'OUTPUT VALIDATION ERROR:' in m['content']]
        if index % 3:
            assert len(repairs) == 1
            assert request['tool_choice'] == 'required'
        else:
            assert repairs == []
            assert request['tool_choice'] == 'auto'
    rejected = [e for e in events if e['type'] == 'reasoning_end' and e['status'] == 'rejected']
    assert len(rejected) == 6
    assert all(e['content'] for e in rejected)
    assert not any(e.get('status') == 'interrupted' for e in events)
    assert db.messages(session['id'])[-1]['content'] == 'Status verified.'


async def test_consecutive_failures_still_stop_without_executing_rejected_calls(tmp_path, monkeypatch):
    import backend.agent as module
    monkeypatch.setenv('FORMA_API_KEY', 'fixture')
    db = Database(tmp_path / 'repair.db')
    settings = SettingsStore(tmp_path / 'settings.json')
    session = db.create_session()
    db.add_message(session['id'], 'user', 'Inspect status')
    captures, events = [], []
    bad = NS(index=0, id='bad', function=NS(name='sw_status', arguments='{"bogus":true}'))
    monkeypatch.setattr(module, 'AsyncOpenAI', lambda **kw: FakeClient([[chunk(calls=[bad])]] * 3, captures))
    bridge = FakeBridge()

    async def emit(event):
        events.append(event)

    with pytest.raises(RuntimeError, match='DSML'):
        await CadAgent(db, settings, bridge, KnowledgeBase(db, settings)).run(session['id'], 'Inspect status', emit)
    assert len(captures) == 3
    assert bridge.calls == [('sw_status', {})]
    assert all(e['status'] == 'rejected' for e in events if e['type'] == 'reasoning_end')
    assert len(db.messages(session['id'])) == 1
