"""Regression fixtures for Harness-inspired stream completion and repair behavior."""
import asyncio
import pytest

from backend.config import Settings
from backend.model_retry import retrying_stream
from backend.model_transport import chunk, claude_request
from test_native_model_protocols import (claude_rows, install_transport,
                                         native_client, sse)


async def test_terminal_chunk_does_not_wait_for_socket_eof():
    closed, retries = [], []

    class Stream:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            closed.append(True)

        async def __aiter__(self):
            yield chunk(content='Complete', finish='stop')
            await asyncio.Event().wait()

    async def create():
        return Stream()

    async def notify(*args):
        retries.append(args)

    async def consume():
        async with retrying_stream(create, notify) as stream:
            return [item async for item in stream]

    result = await asyncio.wait_for(consume(), timeout=0.5)
    assert len(result) == 1
    assert closed == [True]
    assert retries == []


async def test_messages_usage_only_delta_preserves_stop_reason(monkeypatch):
    rows = claude_rows(text='Complete')
    rows.insert(-1, {'type': 'message_delta', 'delta': {'stop_reason': None},
                     'usage': {'output_tokens': 25}})
    install_transport(monkeypatch, lambda request: sse(rows))
    async with native_client(Settings(protocol='claude', base_url='https://provider.test')) as client:
        stream = await client.create(model='fixture', messages=[{'role': 'user', 'content': 'Hi'}],
                                     max_tokens=100, stream=True)
        async with stream:
            chunks = [item async for item in stream]
    assert chunks[-1].choices[0].finish_reason == 'stop'
    assert chunks[-1].usage.completion_tokens == 25


@pytest.mark.parametrize('choice', ['required', {'type': 'function', 'function': {'name': 'inspect'}}])
def test_messages_thinking_does_not_force_incompatible_tool_choice(choice):
    payload = claude_request({'model': 'fixture', 'messages': [{'role': 'user', 'content': 'Inspect'}],
                             'max_tokens': 4096, 'reasoning_effort': 'high', 'tool_choice': choice,
                             'tools': [{'type': 'function', 'function': {'name': 'inspect',
                                        'parameters': {'type': 'object', 'properties': {}}}}]})
    assert payload['thinking'] == {'type': 'adaptive'}
    assert payload['tool_choice'] == {'type': 'auto'}


async def test_invalid_tool_json_reaches_agent_validation_not_transport_failure(monkeypatch):
    call = {'call_id': 'invalid_call', 'name': 'inspect', 'arguments': '{"value":'}
    install_transport(monkeypatch, lambda request: sse(claude_rows(call=call)))
    async with native_client(Settings(protocol='claude', base_url='https://provider.test')) as client:
        stream = await client.create(model='fixture', messages=[{'role': 'user', 'content': 'Inspect'}],
                                     max_tokens=100, stream=True)
        async with stream:
            chunks = [item async for item in stream]
    arguments = ''.join(call.function.arguments for item in chunks
                        for call in item.choices[0].delta.tool_calls)
    assert arguments == '{"value":'
    assert chunks[-1].choices[0].finish_reason == 'tool_calls'
    assert chunks[-1].choices[0].delta.provider_history is None


@pytest.mark.parametrize('failure', ['invalid_json', 'textual_call', 'prose_repair'])
async def test_native_agent_repairs_with_thinking_and_executes_only_valid_calls(monkeypatch, tmp_path, failure):
    import json
    import httpx
    from backend.agent import CadAgent
    from backend.config import SettingsStore
    from backend.knowledge import KnowledgeBase
    from backend.storage import Database

    requests, emitted = [], []
    call = {'call_id': 'valid_call', 'name': 'knowledge_list', 'arguments': '{}'}

    def handler(request):
        body = json.loads(request.content)
        requests.append(body)
        # Emulate a Messages provider that rejects forced tools with thinking.
        if body.get('thinking') and body.get('tool_choice', {}).get('type') in {'any', 'tool'}:
            return httpx.Response(400, json={'error': {'message': 'tool_choice incompatible with thinking'}})
        if len(requests) == 1:
            if failure == 'invalid_json':
                return sse(claude_rows(call={**call, 'call_id': 'rejected_call', 'arguments': '{'}, thinking=True))
            return sse(claude_rows(text='<tool_use>knowledge_list</tool_use>', thinking=True))
        if failure == 'prose_repair' and len(requests) == 2:
            return sse(claude_rows(text='I will check the library.', thinking=True))
        if len(requests) == (3 if failure == 'prose_repair' else 2):
            assert 'APPLICATION OUTPUT VALIDATION ERROR' in json.dumps(body)
            assert body['tool_choice'] == {'type': 'auto'}
            assert not any(m['role'] == 'assistant' for m in body['messages'])
            return sse(claude_rows(call=call, thinking=True))
        assert body['messages'][-1]['content'][0]['tool_use_id'] == 'valid_call'
        assistant = next(m for m in body['messages'] if m['role'] == 'assistant')
        assert assistant['content'][0]['signature'] == 'signed-fixture'
        assert 'APPLICATION OUTPUT VALIDATION ERROR' not in json.dumps(body)
        return sse(claude_rows(text='Library verified.'))

    install_transport(monkeypatch, handler)
    settings = SettingsStore(tmp_path / 'settings.json')
    settings.value = Settings(protocol='claude', base_url='https://provider.test/v1')
    monkeypatch.setenv('FORMA_API_KEY', 'fixture-key')

    class OfflineBridge:
        running = False
        tools = []
        sw_status = {}

    db = Database(tmp_path / 'repair.db')
    session = db.create_session()
    db.add_document('fixture.txt', ['Units are mm.'], None, '')
    db.add_message(session['id'], 'user', 'List uploaded documents.')
    agent = CadAgent(db, settings, OfflineBridge(), KnowledgeBase(db, settings))
    agent.reasoning_effort = 'high'

    async def emit(event):
        emitted.append(event)

    await agent.run(session['id'], 'List uploaded documents.', emit)
    assert len(requests) == (4 if failure == 'prose_repair' else 3)
    results = [e for e in emitted if e['type'] == 'tool_result']
    assert len(results) == 1 and results[0]['name'] == 'knowledge_list'
    assert db.messages(session['id'])[-1]['content'] == 'Library verified.'
    assert not any(e['type'] == 'model_retry' for e in emitted)
