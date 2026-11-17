"""Mutation and no-progress failures at model-only recovery boundaries."""
import copy
from types import SimpleNamespace as NS

import httpx
import pytest

from backend.agent import CadAgent
from backend.config import SettingsStore
from backend.context_policy import compact_context
from backend.knowledge import KnowledgeBase
from backend.request_journal import RequestJournal
from backend.storage import Database
from test_agent import FakeBridge, FakeStream, chunk


async def test_transport_cannot_mutate_retry_input_or_persisted_snapshot(tmp_path, monkeypatch):
    import backend.agent as module
    import backend.model_retry as retry
    monkeypatch.setenv('FORMA_API_KEY', 'fixture')
    db = Database(tmp_path / 'immutable.db')
    settings = SettingsStore(tmp_path / 'settings.json')
    sid = db.create_session()['id']
    db.add_message(sid, 'user', 'keep original request')
    bridge = FakeBridge(); bridge.running = False
    captures = []
    class Client:
        def __init__(self): self.chat = NS(completions=self)
        async def __aenter__(self): return self
        async def __aexit__(self, *args): pass
        async def create(self, **options):
            captures.append(copy.deepcopy(options))
            if len(captures) == 1:
                options['messages'][0]['content'] = 'MUTATED TRANSPORT DATA'
                raise httpx.ReadError('synthetic retry')
            return FakeStream([chunk('Done.')])
    async def wait(delay): pass
    async def emit(event): pass
    monkeypatch.setattr(module, 'AsyncOpenAI', lambda **kw: Client())
    monkeypatch.setattr(retry, 'wait_before_retry', wait)
    await CadAgent(db, settings, bridge, KnowledgeBase(db, settings)).run(sid, 'keep original request', emit)
    assert len(captures) == 2 and captures[0] == captures[1]
    journal = RequestJournal(db)
    records = journal.page(sid)['records']
    assert len(records) == 2 and records[0]['changed'] == []
    for record in records:
        assert 'MUTATED' not in str(journal.detail(sid, record['id']))


@pytest.mark.parametrize('summary', ['', 'x' * 10000])
async def test_forced_overflow_compaction_rejects_empty_or_larger_summary(summary):
    messages = [{'role': 'system', 'content': 'rules'}, {'role': 'user', 'content': 'old ' * 1000},
                {'role': 'assistant', 'content': 'old response'}, {'role': 'user', 'content': 'current'}]
    original = copy.deepcopy(messages)
    class Client:
        def __init__(self): self.chat = NS(completions=self)
        async def create(self, **options):
            return NS(choices=[NS(finish_reason='stop', message=NS(content=summary, tool_calls=[]))])
    with pytest.raises(ValueError):
        await compact_context(Client(), model='fixture', messages=messages, tools=[], context_window=200000,
                              max_output_tokens=1000, instructions='', previous_summary='',
                              system_builder=lambda value: 'rules ' + value, force=True)
    assert messages == original
