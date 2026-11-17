"""Offline wire-contract tests: never contact providers or start a server."""
import json

import httpx
import pytest
from openai import APIStatusError

from backend.config import Settings
from backend.context_policy import summarize_messages
from backend.model_endpoint import chat_endpoint, client_options, models_endpoint
from backend.model_probe import check_model, list_models
from backend.model_transport import (HISTORY_FIELD, ModelClient, claude_request,
                                     responses_request)


def sse(rows):
    return httpx.Response(200, headers={'content-type': 'text/event-stream'},
                          text=''.join('data: ' + json.dumps(row) + '\n\n' for row in rows))


def claude_rows(*, text=None, call=None, thinking=False, finish=True):
    rows = [{'type': 'message_start', 'message': {'usage': {'input_tokens': 15}}}]
    index = 0
    if thinking:
        rows += [{'type': 'content_block_start', 'index': index, 'content_block': {'type': 'thinking', 'thinking': ''}},
                 {'type': 'content_block_delta', 'index': index, 'delta': {'type': 'thinking_delta', 'thinking': 'Check units.'}},
                 {'type': 'content_block_delta', 'index': index, 'delta': {'type': 'signature_delta', 'signature': 'signed-fixture'}},
                 {'type': 'content_block_stop', 'index': index}]
        index += 1
    if call:
        rows += [{'type': 'content_block_start', 'index': index,
                  'content_block': {'type': 'tool_use', 'id': call['call_id'], 'name': call['name'], 'input': {}}},
                 {'type': 'content_block_delta', 'index': index, 'delta': {'type': 'input_json_delta', 'partial_json': call['arguments'][:5]}},
                 {'type': 'content_block_delta', 'index': index, 'delta': {'type': 'input_json_delta', 'partial_json': call['arguments'][5:]}},
                 {'type': 'content_block_stop', 'index': index}]
    if text is not None:
        rows += [{'type': 'content_block_start', 'index': index, 'content_block': {'type': 'text', 'text': ''}},
                 {'type': 'content_block_delta', 'index': index, 'delta': {'type': 'text_delta', 'text': text}},
                 {'type': 'content_block_stop', 'index': index}]
    if finish:
        rows += [{'type': 'message_delta', 'delta': {'stop_reason': 'tool_use' if call else 'end_turn'}, 'usage': {'output_tokens': 20}},
                 {'type': 'message_stop'}]
    return rows


def responses_rows(*, text=None, call=None, thinking=False, finish=True):
    rows, output = [], []
    if thinking:
        output.append({'type': 'reasoning', 'id': 'rs_fixture', 'summary': [], 'encrypted_content': 'opaque-fixture'})
    if call:
        output.append({'type': 'function_call', 'id': 'fc_fixture', 'status': 'completed', **call})
        rows.extend([
            {'type': 'response.output_item.added', 'output_index': len(output) - 1,
             'item': {**output[-1], 'arguments': ''}},
            {'type': 'response.function_call_arguments.delta', 'output_index': len(output) - 1, 'delta': call['arguments'][:5]},
            {'type': 'response.function_call_arguments.delta', 'output_index': len(output) - 1, 'delta': call['arguments'][5:]},
            {'type': 'response.output_item.done', 'output_index': len(output) - 1, 'item': output[-1]}])
    if text is not None:
        rows.append({'type': 'response.output_text.delta', 'delta': text})
        output.append({'type': 'message', 'role': 'assistant', 'id': 'msg_fixture', 'status': 'completed',
                       'content': [{'type': 'output_text', 'text': text, 'annotations': []}]})
    if finish:
        rows.append({'type': 'response.completed', 'response': {'status': 'completed', 'output': output,
                     'usage': {'input_tokens': 15, 'output_tokens': 20}}})
    return rows


def install_transport(monkeypatch, handler):
    import backend.model_transport as module
    import backend.agent as agent
    import backend.model_probe as probe
    def forbidden(**kwargs):
        raise AssertionError('Native protocol unexpectedly selected the Chat Completions client.')
    monkeypatch.setattr(agent, 'AsyncOpenAI', forbidden)
    monkeypatch.setattr(probe, 'AsyncOpenAI', forbidden)
    original = httpx.AsyncClient
    monkeypatch.setattr(module.httpx, 'AsyncClient', lambda **kwargs: original(transport=httpx.MockTransport(handler), **kwargs))


def native_client(config):
    def forbidden(**kwargs):
        raise AssertionError('Non-chat protocols must never instantiate Chat Completions.')
    return ModelClient(config, 'fixture-key', chat_factory=forbidden, chat_options=client_options)


@pytest.mark.parametrize('protocol,base,full,endpoint,catalog', [
    ('responses', 'https://provider.test/v1', False, '/v1/responses', '/v1/models'),
    ('responses', 'https://provider.test/v1/responses', True, '/v1/responses', '/v1/models'),
    ('claude', 'https://provider.test', False, '/v1/messages', '/v1/models'),
    ('claude', 'https://provider.test/v1/', False, '/v1/messages', '/v1/models'),
    ('claude', 'https://provider.test/anthropic', False, '/anthropic/v1/messages', '/anthropic/v1/models'),
    ('claude', 'https://provider.test/anthropic/v1/messages', True, '/anthropic/v1/messages', '/anthropic/v1/models'),
    ('claude', 'https://provider.test/custom/reply/', True, '/custom/reply/', '/custom/models'),
])
def test_native_endpoints(protocol, base, full, endpoint, catalog):
    config = Settings(protocol=protocol, base_url=base, full_endpoint=full)
    assert chat_endpoint(config) == 'https://provider.test' + endpoint
    assert models_endpoint(config) == 'https://provider.test' + catalog


@pytest.mark.parametrize('protocol', ['responses', 'claude'])
async def test_native_probe_two_rounds_auth_usage_and_replay(monkeypatch, protocol):
    bodies = []
    call = {'call_id': 'call_fixture', 'name': 'connection_probe', 'arguments': '{"value":"Dimensions specify exact sizes and units."}'}
    rows = responses_rows if protocol == 'responses' else claude_rows
    def handler(request):
        assert request.url.path == ('/v1/responses' if protocol == 'responses' else '/v1/messages')
        assert request.headers['user-agent'] == 'Forma-test/1'
        if protocol == 'claude':
            assert request.headers['x-api-key'] == 'fixture-key'
            assert request.headers['anthropic-version'] == '2023-06-01'
            assert 'authorization' not in request.headers
        else:
            assert request.headers['authorization'] == 'Bearer fixture-key'
        body = json.loads(request.content)
        bodies.append(body)
        assert 'stream_options' not in body
        if len(bodies) == 1:
            if protocol == 'responses':
                assert body['tools'][0]['name'] == 'connection_probe'
                assert body['tool_choice'] == 'required'
                assert body['max_output_tokens'] == 256000 and body['store'] is False
            else:
                assert body['tools'][0]['input_schema']['type'] == 'object'
                assert body['tool_choice'] == {'type': 'any'}
            return sse(rows(call=call, thinking=True))
        if protocol == 'responses':
            result = next(item for item in body['input'] if item.get('type') == 'function_call_output')
            assert result['call_id'] == 'call_fixture'
            assert any(item.get('encrypted_content') == 'opaque-fixture' for item in body['input'])
            receipt = json.loads(result['output'])['receipt']
        else:
            assistant = next(item for item in body['messages'] if item['role'] == 'assistant')
            assert assistant['content'][0]['signature'] == 'signed-fixture'
            result = body['messages'][-1]['content'][0]
            assert result['type'] == 'tool_result' and result['tool_use_id'] == 'call_fixture'
            receipt = json.loads(result['content'])['receipt']
        return sse(rows(text=receipt))
    install_transport(monkeypatch, handler)
    result = await check_model(config=Settings(protocol=protocol, base_url='https://provider.test/v1', user_agent='Forma-test/1'), key='fixture-key')
    assert result['tool_call_observed'] and result['tool_roundtrip_verified']
    assert result['output_tokens'] == 20 and result['token_source'] == 'usage'
    assert len(bodies) == 2


@pytest.mark.parametrize('protocol', ['responses', 'claude'])
async def test_nonstream_summary_uses_selected_protocol(monkeypatch, protocol):
    seen = []
    def handler(request):
        body = json.loads(request.content)
        seen.append(body)
        assert body['stream'] is False
        if protocol == 'responses':
            return httpx.Response(200, json={'status': 'completed', 'output': [{'type': 'message', 'content': [{'type': 'output_text', 'text': 'Units are mm.'}]}]})
        return httpx.Response(200, json={'stop_reason': 'end_turn', 'content': [{'type': 'text', 'text': 'Units are mm.'}]})
    install_transport(monkeypatch, handler)
    async with native_client(Settings(protocol=protocol, base_url='https://provider.test')) as client:
        summary = await summarize_messages(client, model='fixture', messages=[{'role': 'user', 'content': 'Use mm.'}],
                                           previous_summary='', context_window=200000, max_output_tokens=8192)
    assert summary == 'Units are mm.' and len(seen) == 1


@pytest.mark.parametrize('protocol', ['responses', 'claude'])
async def test_incomplete_stream_is_not_success(monkeypatch, protocol):
    rows = responses_rows if protocol == 'responses' else claude_rows
    install_transport(monkeypatch, lambda request: sse(rows(text='Partial', finish=False)))
    with pytest.raises(ValueError, match='中断'):
        await check_model(config=Settings(protocol=protocol, base_url='https://provider.test'), key='fixture-key')


@pytest.mark.parametrize('protocol', ['responses', 'claude'])
async def test_native_catalog_headers(monkeypatch, protocol):
    def handler(request):
        assert request.url.path == '/v1/models'
        assert request.headers.get('x-api-key') == ('fixture-key' if protocol == 'claude' else None)
        return httpx.Response(200, json={'data': [{'id': 'fixture'}]})
    install_transport(monkeypatch, handler)
    result = await list_models(Settings(protocol=protocol, base_url='https://provider.test/v1'), 'fixture-key')
    assert result['models'] == ['fixture']


def test_native_serialization_system_images_and_multiple_results():
    options = {'model': 'fixture', 'max_tokens': 1000, 'messages': [
        {'role': 'system', 'content': 'Instructions'},
        {'role': 'user', 'content': [{'type': 'text', 'text': 'Inspect'}, {'type': 'image_url', 'image_url': {'url': 'data:image/png;base64,YQ=='}}]},
        {'role': 'assistant', 'content': None, 'tool_calls': [
            {'id': 'a', 'function': {'name': 'one', 'arguments': '{}'}},
            {'id': 'b', 'function': {'name': 'two', 'arguments': '{}'}}]},
        {'role': 'tool', 'tool_call_id': 'a', 'content': 'first'},
        {'role': 'tool', 'tool_call_id': 'b', 'content': 'second'}]}
    claude = claude_request(options)
    assert claude['system'] == [{'type': 'text', 'text': 'Instructions'}]
    assert claude['messages'][0]['content'][1]['source'] == {'type': 'base64', 'media_type': 'image/png', 'data': 'YQ=='}
    assert [b['tool_use_id'] for b in claude['messages'][-1]['content']] == ['a', 'b']
    responses = responses_request(options)
    assert responses['input'][1]['content'][1]['type'] == 'input_image'
    assert [i['call_id'] for i in responses['input'] if i.get('type') == 'function_call_output'] == ['a', 'b']


def test_replay_never_crosses_protocols_or_models():
    message = {'role': 'assistant', 'content': 'Visible', HISTORY_FIELD: {
        'protocol': 'responses', 'model': 'other', 'items': [{'type': 'reasoning', 'encrypted_content': 'opaque'}]}}
    options = {'model': 'fixture', 'max_tokens': 1000, 'messages': [message]}
    assert 'opaque' not in json.dumps(responses_request(options))
    assert 'opaque' not in json.dumps(claude_request(options))


@pytest.mark.parametrize('protocol', ['responses', 'claude'])
async def test_stream_error_and_redirect_cleanup(monkeypatch, protocol):
    requests = []
    def handler(request):
        requests.append(request)
        return httpx.Response(307, headers={'location': 'https://other.test/steal'})
    install_transport(monkeypatch, handler)
    async with native_client(Settings(protocol=protocol, base_url='https://provider.test')) as client:
        with pytest.raises(APIStatusError) as exc:
            await client.chat.completions.create(model='fixture', messages=[{'role': 'user', 'content': 'Hi'}], max_tokens=100, stream=True)
        assert exc.value.status_code == 307
    assert len(requests) == 1


@pytest.mark.parametrize('protocol', ['responses', 'claude'])
async def test_actual_agent_uses_native_protocol(monkeypatch, tmp_path, protocol):
    from backend.agent import CadAgent
    from backend.config import SettingsStore
    from backend.knowledge import KnowledgeBase
    from backend.storage import Database
    rows = responses_rows if protocol == 'responses' else claude_rows
    seen = []
    def handler(request):
        seen.append(request.url.path)
        return sse(rows(text='Native protocol response.'))
    install_transport(monkeypatch, handler)
    settings = SettingsStore(tmp_path / 'settings.json')
    settings.value = Settings(protocol=protocol, base_url='https://provider.test/v1')
    monkeypatch.setenv('FORMA_API_KEY', 'fixture-key')
    class OfflineBridge:
        running = False
        tools = []
        sw_status = {}
    db = Database(tmp_path / 'fixture.db')
    session = db.create_session()
    db.add_message(session['id'], 'user', 'Check protocol.')
    async def emit(event):
        pass
    await CadAgent(db, settings, OfflineBridge(), KnowledgeBase(db, settings)).run(session['id'], 'Check protocol.', emit)
    assert seen == ['/v1/responses' if protocol == 'responses' else '/v1/messages']
    assert db.messages(session['id'])[-1]['content'] == 'Native protocol response.'


@pytest.mark.parametrize('protocol', ['responses', 'claude'])
async def test_actual_agent_native_tool_cycle(monkeypatch, tmp_path, protocol):
    from backend.agent import CadAgent
    from backend.config import SettingsStore
    from backend.knowledge import KnowledgeBase
    from backend.storage import Database
    rows = responses_rows if protocol == 'responses' else claude_rows
    seen = []
    call = {'call_id': 'knowledge_call', 'name': 'knowledge_list', 'arguments': '{}'}
    def handler(request):
        body = json.loads(request.content)
        seen.append(body)
        if len(seen) == 1:
            return sse(rows(call=call, thinking=True))
        encoded = json.dumps(body)
        assert 'knowledge_call' in encoded
        assert ('function_call_output' if protocol == 'responses' else 'tool_result') in encoded
        assert ('opaque-fixture' if protocol == 'responses' else 'signed-fixture') in encoded
        return sse(rows(text='Library checked.'))
    install_transport(monkeypatch, handler)
    settings = SettingsStore(tmp_path / 'settings.json')
    settings.value = Settings(protocol=protocol, base_url='https://provider.test/v1')
    monkeypatch.setenv('FORMA_API_KEY', 'fixture-key')
    class OfflineBridge:
        running = False
        tools = []
        sw_status = {}
    db = Database(tmp_path / 'fixture.db')
    session = db.create_session()
    db.add_document('fixture.txt', ['Units are mm.'], None, '')
    db.add_message(session['id'], 'user', 'List uploaded documents.')
    emitted = []
    async def emit(event):
        emitted.append(event)
    await CadAgent(db, settings, OfflineBridge(), KnowledgeBase(db, settings)).run(session['id'], 'List uploaded documents.', emit)
    assert len(seen) == 2
    assert any(event['type'] == 'tool_result' and event['name'] == 'knowledge_list' for event in emitted)
    assert db.messages(session['id'])[-1]['content'] == 'Library checked.'


@pytest.mark.parametrize('protocol', ['responses', 'claude'])
async def test_native_stream_error_retries_without_replaying_tools(monkeypatch, protocol):
    from backend.model_retry import retrying_stream
    import backend.model_retry as retry
    requests, delays = [], []
    rows = responses_rows if protocol == 'responses' else claude_rows
    def handler(request):
        requests.append(request)
        if len(requests) == 1:
            return sse([{'type': 'error', 'error': {'type': 'overloaded_error', 'message': 'Retry fixture'}}])
        return sse(rows(text='Recovered.'))
    install_transport(monkeypatch, handler)
    async def no_wait(delay):
        delays.append(delay)
    monkeypatch.setattr(retry, 'wait_before_retry', no_wait)
    resets = []
    async def on_retry(attempt, delay):
        resets.append(attempt)
    async with native_client(Settings(protocol=protocol, base_url='https://provider.test/v1')) as client:
        async def create():
            return await client.chat.completions.create(model='fixture', messages=[{'role': 'user', 'content': 'Hi'}], max_tokens=100, stream=True)
        async with retrying_stream(create, on_retry) as stream:
            chunks = [item async for item in stream]
    assert len(requests) == 2 and delays == [1] and resets == [1]
    assert ''.join(choice.delta.content or '' for item in chunks for choice in item.choices) == 'Recovered.'


async def test_claude_unclosed_block_fails(monkeypatch):
    rows = claude_rows(text='Partial')
    rows = [row for row in rows if row['type'] != 'content_block_stop']
    install_transport(monkeypatch, lambda request: sse(rows))
    with pytest.raises(ValueError, match='unfinished'):
        await check_model(config=Settings(protocol='claude', base_url='https://provider.test'), key='fixture-key')


async def test_responses_changed_final_call_fails(monkeypatch):
    call = {'call_id': 'c1', 'name': 'connection_probe', 'arguments': '{"value":"Original"}'}
    rows = responses_rows(call=call)
    rows[-1]['response']['output'] = [{**rows[-1]['response']['output'][0], 'arguments': '{"value":"Changed"}'}]
    install_transport(monkeypatch, lambda request: sse(rows))
    with pytest.raises(ValueError, match='changed'):
        await check_model(config=Settings(protocol='responses', base_url='https://provider.test/v1'), key='fixture-key')


@pytest.mark.parametrize('protocol', ['responses', 'claude'])
async def test_native_stream_closes_on_cancel(monkeypatch, protocol):
    import asyncio
    closed = []
    class WaitingBody(httpx.AsyncByteStream):
        async def __aiter__(self):
            yield b': ping\n\n'
            raise asyncio.CancelledError()
        async def aclose(self):
            closed.append(True)
    install_transport(monkeypatch, lambda request: httpx.Response(200, headers={'content-type': 'text/event-stream'}, stream=WaitingBody()))
    async with native_client(Settings(protocol=protocol, base_url='https://provider.test/v1')) as client:
        stream = await client.chat.completions.create(model='fixture', messages=[{'role': 'user', 'content': 'Hi'}], max_tokens=100, stream=True)
        with pytest.raises(asyncio.CancelledError):
            async with stream:
                async for item in stream:
                    pass
    assert closed == [True]
