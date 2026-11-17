from types import SimpleNamespace as NS

import pytest

from backend.context_policy import (
    compact_context, compression_plan, input_threshold, request_size, summary_transcript,
)
from backend.agent import CadAgent
from backend.config import SettingsStore
from backend.knowledge import KnowledgeBase
from backend.storage import Database
from test_agent import FakeBridge, FakeClient, FakeStream, chunk


def exchange(call_id, text='observed'):
    return [
        {'role': 'assistant', 'content': None, 'tool_calls': [
            {'id': call_id, 'type': 'function', 'function': {'name': 'sw_status', 'arguments': '{}'}}]},
        {'role': 'tool', 'tool_call_id': call_id, 'content': text},
    ]


def test_threshold_and_output_reserve():
    assert input_threshold(200000, 8192) == 160000
    assert input_threshold(10000, 3000) == 7000
    assert input_threshold(10001, 1000) == 8000


def test_budget_includes_system_schemas_instructions_and_images():
    messages = [{'role': 'system', 'content': 'rules'}, {'role': 'user', 'content': 'task'}]
    base = request_size(messages, [])
    assert request_size(messages, [{'schema': 'x' * 2000}]) > base + 400
    assert request_size(messages, [], 'rules' * 1000) > base + 1000
    assert request_size([{'role': 'user', 'content': [
        {'type': 'image_url', 'image_url': {'url': 'data:base64,' + 'x' * 10000}}]}], []) < 1100


def test_plan_keeps_current_request_and_final_atomic_exchange():
    user = {'role': 'user', 'content': 'Keep 15 mm height'}
    messages = [{'role': 'system', 'content': 'rules'}, user,
                *exchange('old', 'x' * 20000), *exchange('latest')]
    removed, kept = compression_plan(messages, [], 1000, '')
    assert removed == [2, 3]
    assert kept == [messages[0], user, *messages[-2:]]
    assert messages[3]['content'] == 'x' * 20000


def test_preserves_all_results_in_parallel_batch():
    batch = exchange('a')
    batch[0]['tool_calls'].extend(exchange('b')[0]['tool_calls'])
    batch.append(exchange('b')[1])
    messages = [{'role': 'system', 'content': 'rules'}, {'role': 'user', 'content': 'task'},
                *exchange('old', 'x' * 20000), *batch]
    _, kept = compression_plan(messages, [], 1000, '')
    assert kept[-3:] == batch


def test_visual_feedback_does_not_replace_pinned_user_request():
    user = {'role': 'user', 'content': 'Original requirements'}
    messages = [{'role': 'system', 'content': 'rules'}, user,
                *exchange('old', 'x' * 20000),
                {'role': 'user', 'content': [{'type': 'text', 'text': 'Visual feedback'}]},
                *exchange('latest')]
    _, kept = compression_plan(messages, [], 1000, '', protected_user_index=1)
    assert user in kept


def test_incomplete_exchange_is_never_split():
    with pytest.raises(ValueError, match='incomplete'):
        compression_plan([{'role': 'system', 'content': 'rules'}, *exchange('a')[:1]], [], 10, '')


def test_summary_excludes_image_bytes_but_keeps_arguments_and_results():
    messages = [*exchange('a'), {'role': 'user', 'content': [
        {'type': 'image_url', 'image_url': {'url': 'secret-image-bytes'}}]}]
    text = summary_transcript(messages)
    assert 'secret-image-bytes' not in text
    assert 'tool_calls' in text and 'tool_call_id' in text and 'observed' in text


@pytest.mark.parametrize('boundary_offset,expected', [(-1, False), (0, True), (1, True)])
async def test_exact_threshold_boundary(monkeypatch, boundary_offset, expected):
    import backend.context_policy as module
    messages = [{'role': 'system', 'content': 'rules'},
                {'role': 'assistant', 'content': 'x' * 10000},
                {'role': 'user', 'content': 'latest request'}]
    original_size = request_size(messages, [])
    monkeypatch.setattr(module, 'input_threshold', lambda *_: original_size - boundary_offset)
    captures = []
    client = FakeClient(['Verified old facts.'] * 4, captures)
    compacted, summary, removed = await compact_context(
        client, model='fixture', messages=messages, tools=[], context_window=20000,
        max_output_tokens=2000, instructions='', previous_summary='',
        system_builder=lambda s: 'rules ' + s, history_only=True)
    assert bool(captures) is expected
    assert bool(removed) is expected
    assert compacted[-1] == messages[-1]
    if expected:
        assert summary == 'Verified old facts.'


@pytest.mark.parametrize('summary', ['', ' ', 'x' * 20000])
async def test_bad_summary_preserves_input(summary):
    messages = [{'role': 'system', 'content': 'rules'},
                {'role': 'assistant', 'content': 'x' * 13000},
                {'role': 'user', 'content': 'latest request'}]
    client = FakeClient([summary], [])
    with pytest.raises(ValueError):
        await compact_context(
            client, model='fixture', messages=messages, tools=[], context_window=4000,
            max_output_tokens=500, instructions='', previous_summary='',
            system_builder=lambda s: 'rules ' + s, history_only=True)
    assert len(messages[1]['content']) == 13000


async def test_oversized_pinned_request_stops_before_api_call():
    captures = []
    with pytest.raises(ValueError, match='input budget'):
        await compact_context(
            FakeClient([], captures), model='fixture',
            messages=[{'role': 'system', 'content': 'rules'}, {'role': 'user', 'content': 'x' * 20000}],
            tools=[], context_window=4000, max_output_tokens=500, instructions='',
            previous_summary='', system_builder=lambda s: 'rules ' + s)
    assert not captures


async def test_agent_compresses_large_history_and_persists_only_summary(tmp_path, monkeypatch):
    import backend.agent as module
    monkeypatch.setenv('FORMA_API_KEY', 'fixture')
    db = Database(tmp_path / 'context.db')
    config = SettingsStore(tmp_path / 'settings.json')
    config.value.context_window = 12000
    config.value.max_output_tokens = 1000
    session = db.create_session()
    for i in range(14):
        db.add_message(session['id'], 'user' if i % 2 == 0 else 'assistant', f'{i}: ' + 'x' * 4000)
    db.add_message(session['id'], 'user', 'Preserve latest request: 15 mm')
    captures = []

    class Client(FakeClient):
        async def create(self, **kwargs):
            captures.append(kwargs)
            if kwargs.get('stream'):
                return FakeStream([chunk('Verified response.')])
            return NS(choices=[NS(message=NS(content='Previous goal: 100 mm plate. Live state unknown.'))])

    monkeypatch.setattr(module, 'AsyncOpenAI', lambda **kw: Client([], captures))
    bridge = FakeBridge()
    bridge.running = False
    agent = CadAgent(db, config, bridge, KnowledgeBase(db, config))
    agent.system = 'Fixture system rules'
    events = []

    async def emit(event):
        events.append(event)

    await agent.run(session['id'], 'Preserve latest request: 15 mm', emit)
    assert len(db.messages(session['id'])) == 16
    assert db.session(session['id'])['summary_through'] > 0
    assert '100 mm plate' in captures[-1]['messages'][0]['content']
    assert captures[-1]['messages'][-1]['content'] == 'Preserve latest request: 15 mm'
    assert any(not request.get('stream') for request in captures)
    for request in captures:
        assert request_size(request['messages'], request.get('tools', [])) + request['max_tokens'] < 12000
    assert any('压缩完成' in e.get('message', '') for e in events)


async def test_agent_compresses_during_tool_loop_without_replaying_calls(tmp_path, monkeypatch):
    import backend.agent as module
    monkeypatch.setenv('FORMA_API_KEY', 'fixture')
    db = Database(tmp_path / 'runtime-context.db')
    config = SettingsStore(tmp_path / 'settings.json')
    config.value.context_window = 10000
    config.value.max_output_tokens = 1000
    session = db.create_session()
    db.add_message(session['id'], 'user', 'Inspect only; retain 15 mm requirement.')
    captures = []
    stream_count = 0

    class Client(FakeClient):
        async def create(self, **kwargs):
            nonlocal stream_count
            captures.append(kwargs)
            if not kwargs.get('stream'):
                return NS(choices=[NS(message=NS(content='Earlier inspection completed. No edits. Requirement: 15 mm.'))])
            stream_count += 1
            if stream_count <= 3:
                call = NS(index=0, id=f'inspect_{stream_count}',
                          function=NS(name='sw_status', arguments='{}'))
                return FakeStream([chunk(calls=[call])])
            return FakeStream([chunk('Inspection completed; no geometry changed.')])

    class Bridge(FakeBridge):
        async def call(self, name, arguments):
            self.calls.append((name, arguments))
            return {'content': 'Observed fixture status. ' + 'x' * 17000, 'is_error': False}

    monkeypatch.setattr(module, 'AsyncOpenAI', lambda **kw: Client([], captures))
    bridge = Bridge()
    agent = CadAgent(db, config, bridge, KnowledgeBase(db, config))
    agent.system = 'Fixture system rules'

    async def emit(event):
        pass

    await agent.run(session['id'], 'Inspect only; retain 15 mm requirement.', emit)
    summaries = [r for r in captures if not r.get('stream')]
    requests = [r for r in captures if r.get('stream')]
    assert len(summaries) >= 2
    assert len(requests) == 4
    assert len(bridge.calls) == 5  # Three requested inspections plus initial/final status.
    assert db.session(session['id'])['summary_through'] == 0
    for request in requests:
        assert any(m.get('content') == 'Inspect only; retain 15 mm requirement.'
                   for m in request['messages'])
        pending = set()
        for message in request['messages']:
            pending.update(c['id'] for c in message.get('tool_calls', []))
            if message['role'] == 'tool':
                assert message['tool_call_id'] in pending
                pending.remove(message['tool_call_id'])
        assert not pending
        assert request_size(request['messages'], request.get('tools', [])) + request['max_tokens'] < 10000
    assert requests[-1]['messages'][-1]['tool_call_id'] == 'inspect_3'
