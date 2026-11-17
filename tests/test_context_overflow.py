"""Explicit overflow recovery through real agent/native transports, fully offline."""
import asyncio
import copy
import json
from types import SimpleNamespace as NS

import httpx
import pytest
from openai import APIStatusError

from backend.agent import CadAgent
from backend.config import Settings, SettingsStore
from backend.context_overflow import is_context_overflow
from backend.context_policy import compact_context
from backend.knowledge import KnowledgeBase
from backend.request_journal import RequestJournal
from backend.storage import Database
from test_agent import FakeBridge, FakeStream, chunk
from test_native_model_protocols import install_transport, sse, responses_rows, claude_rows


def overflow(body=None, status=400):
    return APIStatusError('fixture overflow', response=httpx.Response(status, request=httpx.Request('POST', 'https://fixture.test')),
                          body=body or {'error': {'code': 'context_length_exceeded', 'message': 'Too long'}})


@pytest.mark.parametrize('body,status,expected', [
    ({'error': {'code': 'context_length_exceeded'}}, 400, True),
    ({'response': {'error': {'code': 'context_window_exceeded'}}}, 400, True),
    ({'error': {'type': 'invalid_request_error', 'message': 'prompt is too long: 123 tokens'}}, 400, True),
    ({'error': {'type': 'invalid_request_error', 'message': 'invalid tool schema'}}, 400, False),
    ({'error': {'code': 'context_length_exceeded'}}, 429, False),
    ({'error': {'type': 'authentication_error'}}, 401, False),
])
def test_overflow_classification_is_narrow(body, status, expected):
    assert is_context_overflow(overflow(body, status)) is expected
    assert not is_context_overflow(ValueError('context_length_exceeded'))
    assert not is_context_overflow(asyncio.CancelledError())


@pytest.mark.parametrize('protocol,delivery', [('chat', 'http'), ('responses', 'http'), ('responses', 'stream'),
                                               ('claude', 'http'), ('claude', 'stream')])
async def test_overflow_after_tool_result_shrinks_request_without_replaying_operation(tmp_path, monkeypatch, protocol, delivery):
    import backend.agent as module
    monkeypatch.setenv('FORMA_API_KEY', 'fixture')
    db = Database(tmp_path / 'overflow.db')
    settings = SettingsStore(tmp_path / 'settings.json')
    settings.value = Settings(protocol=protocol, base_url='https://fixture.test', context_window=200000, max_output_tokens=8192)
    sid = db.create_session()['id']
    for i in range(6):
        db.add_message(sid, 'user', f'old user {i} ' + 'historical requirement ' * 200)
        db.add_message(sid, 'assistant', 'historical observation ' * 200)
    db.add_message(sid, 'user', 'Perform one fixture change, then report')
    bridge = FakeBridge()
    bridge.tools = [*bridge.tools, {'name': 'fixture_change', 'description': 'fixture change',
                    'annotations': {'destructiveHint': False},
                    'inputSchema': {'type': 'object', 'properties': {}, 'additionalProperties': False}}]
    requests, summaries, events = [], [], []
    wire_call = {'call_id': 'change_once', 'name': 'fixture_change', 'arguments': '{}'}
    rows = responses_rows if protocol == 'responses' else claude_rows

    def reply(options):
        if not options.get('stream'):
            summaries.append(copy.deepcopy(options))
            if protocol == 'chat':
                return NS(choices=[NS(finish_reason='stop', message=NS(content='Historical context condensed.', tool_calls=[]))])
            if protocol == 'responses':
                return httpx.Response(200, json={'status': 'completed', 'output': [{'type': 'message', 'content': [{'type': 'output_text', 'text': 'Historical context condensed.'}]}]})
            return httpx.Response(200, json={'stop_reason': 'end_turn', 'content': [{'type': 'text', 'text': 'Historical context condensed.'}]})
        requests.append(copy.deepcopy(options))
        if len(requests) == 1:
            if protocol == 'chat':
                call = NS(index=0, id='change_once', function=NS(name='fixture_change', arguments='{}'))
                return FakeStream([chunk(calls=[call])])
            return sse(rows(call=wire_call))
        if len(requests) == 2:
            if protocol == 'chat':
                raise overflow()
            error = {'code': 'context_length_exceeded', 'type': 'invalid_request_error', 'message': 'prompt is too long'}
            return (httpx.Response(400, json={'error': error}) if delivery == 'http' else
                    sse([{'type': 'error', 'error': error}]))
        assert len(requests) == 3
        return FakeStream([chunk('Completed after context recovery.')]) if protocol == 'chat' else sse(rows(text='Completed after context recovery.'))

    if protocol == 'chat':
        class Client:
            def __init__(self): self.chat = NS(completions=self)
            async def __aenter__(self): return self
            async def __aexit__(self, *args): pass
            async def create(self, **options): return reply(options)
        monkeypatch.setattr(module, 'AsyncOpenAI', lambda **kw: Client())
    else:
        install_transport(monkeypatch, lambda request: reply(json.loads(request.content)))
    async def emit(event): events.append(event)
    await CadAgent(db, settings, bridge, KnowledgeBase(db, settings)).run(sid, 'Perform one fixture change, then report', emit)
    assert [name for name, _ in bridge.calls].count('fixture_change') == 1
    assert len(requests) == 3 and summaries
    assert len(json.dumps(requests[2])) < len(json.dumps(requests[1]))
    assert 'change_once' in json.dumps(requests[2])
    assert 'Completed after context recovery.' == db.messages(sid)[-1]['content']
    journal = RequestJournal(db)
    records = journal.page(sid)['records']
    assert len(records) == 3
    assert [r['round'] for r in records] == [2, 2, 1]
    detail = journal.detail(sid, records[0]['id'])
    assert detail['snapshot']['context']['replacements'][-1]['reason'] == 'provider_context_overflow'


async def test_force_compaction_without_removable_exchange_preserves_history():
    messages = [{'role': 'system', 'content': 'system'}, {'role': 'user', 'content': 'current'}]
    original = copy.deepcopy(messages)
    result, summary, removed = await compact_context(None, model='fixture', messages=messages, tools=[],
        context_window=10000, max_output_tokens=1000, instructions='', previous_summary='',
        system_builder=lambda text: text, force=True)
    assert not removed and result == original and messages == original


@pytest.mark.parametrize('cancel', [False, True])
async def test_overflow_recovery_is_bounded_and_cancellation_wins(tmp_path, monkeypatch, cancel):
    import backend.agent as module
    monkeypatch.setenv('FORMA_API_KEY', 'fixture')
    db = Database(tmp_path / 'bounded.db')
    settings = SettingsStore(tmp_path / 'settings.json')
    sid = db.create_session()['id']
    db.add_message(sid, 'user', 'current request')
    bridge = FakeBridge(); bridge.running = False
    calls, compactions = [], []
    class Client:
        def __init__(self): self.chat = NS(completions=self)
        async def __aenter__(self): return self
        async def __aexit__(self, *args): pass
        async def create(self, **options):
            calls.append(options)
            raise overflow()
    async def compact(*args, **kwargs):
        compactions.append(kwargs)
        if cancel: raise asyncio.CancelledError()
        return kwargs['messages'], 'summary', [1]
    monkeypatch.setattr(module, 'AsyncOpenAI', lambda **kw: Client())
    monkeypatch.setattr(module, 'compact_context', compact)
    async def emit(event): pass
    with pytest.raises(asyncio.CancelledError if cancel else APIStatusError):
        await CadAgent(db, settings, bridge, KnowledgeBase(db, settings)).run(sid, 'current request', emit)
    assert len(calls) == (1 if cancel else 3)
    assert len(compactions) == (1 if cancel else 2)
    assert not bridge.calls
