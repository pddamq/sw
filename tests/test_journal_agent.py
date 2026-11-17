import pytest
from types import SimpleNamespace as NS

from backend.agent import CadAgent
from backend.config import SettingsStore
from backend.conversation_journal import ConversationJournal
from backend.context_policy import compression_plan
from backend.knowledge import KnowledgeBase
from backend.storage import Database
from test_agent import FakeBridge, FakeClient, chunk


def test_compaction_never_splits_a_persisted_user_anchor():
    messages = [{'role': 'system', 'content': 'rules'},
                {'role': 'user', 'content': 'old'},
                {'role': 'assistant', 'content': 'x' * 10000},
                {'role': 'assistant', 'content': 'y' * 10000},
                {'role': 'user', 'content': 'new'}]
    removed, kept = compression_plan(messages, [], 1000, '', history_only=True,
                                     group_ids={1: 1, 2: 1, 3: 1, 4: 4})
    assert removed == [1, 2, 3]
    assert kept == [messages[0], messages[4]]


async def test_next_task_restores_calls_and_does_not_reexecute_them(tmp_path, monkeypatch):
    import backend.agent as module
    db = Database(tmp_path / 'restart.db')
    settings = SettingsStore(tmp_path / 'settings.json')
    monkeypatch.setenv('FORMA_API_KEY', 'fixture')
    sid = db.create_session()['id']
    db.add_message(sid, 'user', 'Inspect')
    bridge = FakeBridge()
    captures, events = [], []
    replies = [[chunk(calls=[NS(index=0, id='first', function=NS(name='sw_status', arguments='{}'))])],
               [chunk('Observed.')], [chunk('History acknowledged.')]]
    client = FakeClient(replies, captures)
    monkeypatch.setattr(module, 'AsyncOpenAI', lambda **kw: client)
    async def emit(event):
        if event['type'] == 'model_attempt' and event['phase'] == 'committed':
            assert ConversationJournal(db).records(sid)
        events.append(event)
    await CadAgent(db, settings, bridge, KnowledgeBase(db, settings)).run(sid, 'Inspect', emit)
    previous_count = len(bridge.calls)
    db.add_message(sid, 'user', 'Recall observations')
    await CadAgent(db, settings, bridge, KnowledgeBase(db, settings)).run(sid, 'Recall observations', emit)
    assert len(bridge.calls) == previous_count + 2  # Only standard status refreshes.
    history = captures[-1]['messages']
    assert sum(bool(m.get('tool_calls')) for m in history) == 1
    assert sum(m.get('content') == 'Observed.' for m in history) == 1


async def test_failed_durable_accept_never_commits_or_dispatches_response(tmp_path, monkeypatch):
    import backend.agent as module
    db = Database(tmp_path / 'failure.db')
    settings = SettingsStore(tmp_path / 'settings.json')
    monkeypatch.setenv('FORMA_API_KEY', 'fixture')
    sid = db.create_session()['id']
    db.add_message(sid, 'user', 'Inspect')
    bridge, events = FakeBridge(), []
    responses = [[chunk('Uncommitted text', [NS(index=0, id='a', function=NS(name='sw_status', arguments='{}'))])]]
    monkeypatch.setattr(module, 'AsyncOpenAI', lambda **kw: FakeClient(responses, []))
    def fail(*args, **kwargs):
        raise OSError('synthetic storage failure')
    monkeypatch.setattr(ConversationJournal, 'accept', fail)
    async def emit(event):
        events.append(event)
    with pytest.raises(OSError, match='storage failure'):
        await CadAgent(db, settings, bridge, KnowledgeBase(db, settings)).run(sid, 'Inspect', emit)
    assert bridge.calls == [('sw_status', {})]
    assert not any(e['type'] in {'answer', 'token'} for e in events)
    assert [e for e in events if e['type'] == 'model_attempt'][-1]['phase'] == 'interrupted'
    preview = next(e for e in events if e['type'] == 'answer_preview_end')
    assert preview['status'] == 'interrupted'
    assert preview['content'] == 'Uncommitted text'
    assert all(m['role'] != 'assistant' for m in db.messages(sid))
