import pytest
from types import SimpleNamespace as NS

from backend.agent import CadAgent
from backend.config import SettingsStore
from backend.knowledge import KnowledgeBase
from backend.storage import Database
from test_agent import FakeBridge, FakeClient, chunk


@pytest.mark.parametrize('marker', ['<|DSML|calls>', '<｜DSML｜function_calls>', '< | DSML | invoke name="sw_status">'])
async def test_text_markup_retries_using_real_tool_calls(tmp_path, monkeypatch, marker):
    import backend.agent as module
    monkeypatch.setenv('FORMA_API_KEY', 'fixture')
    db = Database(tmp_path / 'protocol.db')
    config = SettingsStore(tmp_path / 'settings.json')
    session = db.create_session()
    db.add_message(session['id'], 'user', 'Check SW status')
    captures = []
    call = NS(index=0, id='actual_call', function=NS(name='sw_status', arguments='{}'))
    responses = [[chunk(marker[:4]), chunk(marker[4:])], [chunk(calls=[call])], [chunk('Status checked.')]]
    monkeypatch.setattr(module, 'AsyncOpenAI', lambda **kw: FakeClient(responses, captures))
    bridge = FakeBridge()
    events = []
    async def emit(event):
        events.append(event)
    await CadAgent(db, config, bridge, KnowledgeBase(db, config)).run(session['id'], 'Check SW status', emit)
    assert len(captures) == 3
    assert 'None of those textual operations were executed' in captures[1]['messages'][-1]['content']
    assert bridge.calls == [('sw_status', {}), ('sw_status', {}), ('sw_status', {})]
    assert any(e['type'] == 'status' and '1/2' in e['message'] for e in events)
    assert captures[2]['messages'][-1]['role'] == 'tool'


async def test_repeated_markup_fails_explicitly_without_execution(tmp_path, monkeypatch):
    import backend.agent as module
    monkeypatch.setenv('FORMA_API_KEY', 'fixture')
    db = Database(tmp_path / 'protocol.db')
    config = SettingsStore(tmp_path / 'settings.json')
    session = db.create_session()
    db.add_message(session['id'], 'user', 'Build a part')
    captures = []
    text = '<|DSML|calls><|DSML|invoke name="feature_extrude">'
    monkeypatch.setattr(module, 'AsyncOpenAI', lambda **kw: FakeClient([[chunk(text)]] * 3, captures))
    bridge = FakeBridge()
    async def emit(event):
        pass
    with pytest.raises(RuntimeError, match='DSML'):
        await CadAgent(db, config, bridge, KnowledgeBase(db, config)).run(session['id'], 'Build a part', emit)
    assert len(captures) == 3
    assert bridge.calls == [('sw_status', {})]


async def test_native_calls_with_markup_are_not_replayed(tmp_path, monkeypatch):
    import backend.agent as module
    monkeypatch.setenv('FORMA_API_KEY', 'fixture')
    db = Database(tmp_path / 'protocol.db')
    config = SettingsStore(tmp_path / 'settings.json')
    session = db.create_session()
    db.add_message(session['id'], 'user', 'Check status')
    captures = []
    call = NS(index=0, id='actual_call', function=NS(name='sw_status', arguments='{}'))
    responses = [[chunk('<|DSML|calls>', [call])], [chunk(calls=[call])], [chunk('Done.')]]
    monkeypatch.setattr(module, 'AsyncOpenAI', lambda **kw: FakeClient(responses, captures))
    bridge = FakeBridge()
    async def emit(event):
        pass
    await CadAgent(db, config, bridge, KnowledgeBase(db, config)).run(session['id'], 'Check status', emit)
    assert len(captures) == 3
    assert not any(m.get('tool_calls') for m in captures[1]['messages'])
    assert bridge.calls == [('sw_status', {}), ('sw_status', {}), ('sw_status', {})]
