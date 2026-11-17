"""Retry regressions using the real SDK against synthetic HTTP; no CAD or network."""
import asyncio
import json
from types import SimpleNamespace as NS

import httpx
import pytest

from backend.agent import CadAgent
from backend.config import SettingsStore
from backend.context_policy import summarize_messages
from backend.execution import ExecutionHalted
from backend.knowledge import KnowledgeBase
from backend.model_retry import retry_model_request, retrying_stream
from backend.storage import Database
from test_agent import FakeBridge, FakeClient, chunk
from test_protocol_recovery_sdk import call_deltas, mock_client, stream_response


@pytest.fixture(autouse=True)
def fast_backoff(monkeypatch):
    import backend.model_retry as module
    delays = []

    async def wait(delay):
        delays.append(delay)

    monkeypatch.setattr(module, 'wait_before_retry', wait)
    return delays


def setup_agent(tmp_path, monkeypatch, handler, bridge=None):
    import backend.agent as module
    monkeypatch.setenv('FORMA_API_KEY', 'fixture')
    mock_client(monkeypatch, module, handler)
    db = Database(tmp_path / 'retry.db')
    config = SettingsStore(tmp_path / 'settings.json')
    config.value.model_retry_jitter = 0
    session = db.create_session()
    db.add_message(session['id'], 'user', 'Inspect the fixture')
    bridge = bridge or FakeBridge()
    agent = CadAgent(db, config, bridge, KnowledgeBase(db, config))
    events = []

    async def emit(event):
        events.append(event)

    return agent, db, session['id'], bridge, events, emit


@pytest.mark.parametrize('status', [408, 429, 500, 502, 503, 504])
async def test_http_transient_failure_retries_identical_request(tmp_path, monkeypatch, status, fast_backoff):
    requests = []

    def handler(request):
        requests.append(json.loads(request.content))
        if len(requests) == 1:
            return httpx.Response(status, json={'error': {'message': 'Temporary fixture failure'}})
        return stream_response([{'content': 'Recovered answer'}])

    agent, db, sid, bridge, events, emit = setup_agent(tmp_path, monkeypatch, handler)
    await agent.run(sid, 'Inspect the fixture', emit)
    assert len(requests) == 2
    assert requests[0] == requests[1]
    assert fast_backoff == [1]
    assert [e['attempt'] for e in events if e['type'] == 'model_retry'] == [1]
    assert db.messages(sid)[-1]['content'] == 'Recovered answer'
    assert bridge.calls == [('sw_status', {}), ('sw_status', {})]


@pytest.mark.parametrize('status', [400, 401, 403, 404, 422])
async def test_configuration_errors_do_not_retry(tmp_path, monkeypatch, status, fast_backoff):
    requests = []

    def handler(request):
        requests.append(request)
        return httpx.Response(status, json={'error': {'message': 'Invalid fixture config'}})

    agent, _, sid, bridge, events, emit = setup_agent(tmp_path, monkeypatch, handler)
    with pytest.raises(Exception):
        await agent.run(sid, 'Inspect the fixture', emit)
    assert len(requests) == 1
    assert not fast_backoff
    assert not any(e['type'] == 'model_retry' for e in events)
    assert bridge.calls == [('sw_status', {})]


@pytest.mark.parametrize('failure', ['connect', 'timeout', 'eof'])
async def test_exactly_five_retries_then_stop(tmp_path, monkeypatch, failure, fast_backoff):
    requests = []

    def handler(request):
        requests.append(request)
        if failure == 'connect':
            raise httpx.ConnectError('Fixture disconnected', request=request)
        if failure == 'timeout':
            raise httpx.ReadTimeout('Fixture timeout', request=request)
        return stream_response(call_deltas('sw_status', '{}'), finish=None)

    agent, _, sid, bridge, events, emit = setup_agent(tmp_path, monkeypatch, handler)
    with pytest.raises(RuntimeError, match='5 次'):
        await agent.run(sid, 'Inspect the fixture', emit)
    assert len(requests) == 6
    assert fast_backoff == [1, 2, 4, 8, 16]
    assert [e['attempt'] for e in events if e['type'] == 'model_retry'] == [1, 2, 3, 4, 5]
    assert bridge.calls == [('sw_status', {})]


class BrokenBody(httpx.AsyncByteStream):
    def __init__(self, payload):
        self.payload = payload
        self.closed = False

    async def __aiter__(self):
        yield self.payload
        raise httpx.ReadError('Fixture socket interrupted')

    async def aclose(self):
        self.closed = True


@pytest.mark.parametrize('socket_error', [False, True])
async def test_partial_text_reasoning_and_calls_reset_without_duplicate_execution(tmp_path, monkeypatch, socket_error):
    requests = []
    broken = BrokenBody(stream_response([
        {'reasoning_content': 'Discard this reasoning'}, {'content': 'Discard this answer'},
        *call_deltas('sw_status', '{}', id='discarded_call'),
    ], finish=None).content)

    def handler(request):
        requests.append(json.loads(request.content))
        if len(requests) == 1:
            if socket_error:
                return httpx.Response(200, headers={'content-type': 'text/event-stream'}, stream=broken)
            return stream_response([
                {'reasoning_content': 'Discard this reasoning'}, {'content': 'Discard this answer'},
                *call_deltas('sw_status', '{}', id='discarded_call'),
            ], finish=None)
        if len(requests) == 2:
            return stream_response([{'reasoning_content': 'Fresh reasoning'},
                                    *call_deltas('sw_status', '{}', id='fresh_call')], 'tool_calls')
        return stream_response([{'content': 'Verified final answer'}])

    agent, db, sid, bridge, events, emit = setup_agent(tmp_path, monkeypatch, handler)
    await agent.run(sid, 'Inspect the fixture', emit)
    assert len(requests) == 3
    assert requests[0] == requests[1]
    assert requests[2]['messages'][-1]['tool_call_id'] == 'fresh_call'
    assert 'Discard this' not in json.dumps(requests[2])
    assert bridge.calls == [('sw_status', {})] * 3
    assert db.messages(sid)[-1]['content'] == 'Verified final answer'
    assert next(e for e in events if e['type'] == 'model_retry')['content'] == ''
    if socket_error:
        assert broken.closed


async def test_successful_previous_tool_is_not_replayed_on_next_round_failure(tmp_path, monkeypatch):
    requests = []

    def handler(request):
        requests.append(json.loads(request.content))
        if len(requests) == 1:
            return stream_response([{'content': 'Inspecting now'}, *call_deltas('sw_status', '{}')], 'tool_calls')
        if len(requests) == 2:
            return stream_response([{'content': 'Interrupted second round'}], finish=None)
        return stream_response([{'content': 'Inspection complete'}])

    agent, db, sid, bridge, events, emit = setup_agent(tmp_path, monkeypatch, handler)
    await agent.run(sid, 'Inspect the fixture', emit)
    assert requests[1] == requests[2]
    assert requests[2]['messages'][-1]['role'] == 'tool'
    assert bridge.calls == [('sw_status', {})] * 3
    assert db.messages(sid)[-1]['content'] == 'Inspecting now\n\nInspection complete'
    retry = next(e for e in events if e['type'] == 'model_retry')
    assert retry['round'] == 2
    assert retry['content'] == 'Inspecting now\n\n'


@pytest.mark.parametrize('finish', ['length', 'content_filter'])
async def test_model_output_limit_is_not_treated_as_connection_loss(tmp_path, monkeypatch, finish, fast_backoff):
    calls = []

    def handler(request):
        calls.append(request)
        return stream_response(call_deltas('sw_status', '{}'), finish)

    agent, _, sid, bridge, _, emit = setup_agent(tmp_path, monkeypatch, handler)
    with pytest.raises(RuntimeError, match='未完整'):
        await agent.run(sid, 'Inspect the fixture', emit)
    assert len(calls) == 1
    assert not fast_backoff
    assert bridge.calls == [('sw_status', {})]


async def test_cancellation_during_backoff_stops_without_another_request(tmp_path, monkeypatch):
    import backend.model_retry as module
    entered = asyncio.Event()
    requests = []

    async def wait(delay):
        entered.set()
        await asyncio.Event().wait()

    def handler(request):
        requests.append(request)
        raise httpx.ConnectError('Fixture disconnected', request=request)

    monkeypatch.setattr(module, 'wait_before_retry', wait)
    agent, _, sid, bridge, _, emit = setup_agent(tmp_path, monkeypatch, handler)
    task = asyncio.create_task(agent.run(sid, 'Inspect the fixture', emit))
    await asyncio.wait_for(entered.wait(), timeout=5)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert len(requests) == 1
    assert bridge.calls == [('sw_status', {})]


async def test_tool_uncertainty_never_triggers_model_retry(tmp_path, monkeypatch, fast_backoff):
    class Control:
        recovery_context = None

        def permits(self, name):
            return True

        def confirmation_reason(self, name, args):
            return None

        async def invoke(self, name, args, purpose=None):
            if name == 'model_info':
                raise ExecutionHalted('Fixture tool outcome unknown')
            return {'content': '{}', 'is_error': False}

    bridge = FakeBridge()
    bridge.tools = [*bridge.tools, {'name': 'model_info', 'inputSchema': {'type': 'object'}}]
    requests = []

    def handler(request):
        requests.append(request)
        return stream_response(call_deltas('model_info', '{}'), 'tool_calls')

    agent, _, sid, _, _, emit = setup_agent(tmp_path, monkeypatch, handler, bridge)
    agent.control = Control()
    with pytest.raises(ExecutionHalted):
        await agent.run(sid, 'Inspect the fixture', emit)
    assert len(requests) == 1
    assert not fast_backoff


async def test_summary_transport_failure_retries_same_block():
    captures, retries = [], []

    class Client(FakeClient):
        async def create(self, **kwargs):
            captures.append(kwargs)
            if len(captures) == 1:
                raise httpx.ConnectError('Fixture summary connection')
            return NS(choices=[NS(message=NS(content='Verified memory'))])

    async def notify(attempt, delay):
        retries.append((attempt, delay))

    summary = await summarize_messages(Client([], []), model='fixture',
        messages=[{'role': 'user', 'content': 'Original requirements'}], previous_summary='',
        context_window=20000, max_output_tokens=2000, on_retry=notify)
    assert summary == 'Verified memory'
    assert captures[0] == captures[1]
    assert retries == [(1, 1)]


async def test_consumer_error_is_not_retried_and_closes_source(fast_backoff):
    closed = []

    class Stream:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            closed.append(True)

        async def __aiter__(self):
            yield chunk('one')

    async def create():
        return Stream()

    async def notify(*args):
        pytest.fail('Consumer errors must not reconnect the model')

    with pytest.raises(httpx.ConnectError):
        async with retrying_stream(create, notify) as stream:
            async for _ in stream:
                raise httpx.ConnectError('UI connection failed, not model connection')
    assert closed == [True]
    assert not fast_backoff


async def test_nonstream_retry_bound(fast_backoff):
    requests, retries = [], []

    async def operation():
        requests.append(True)
        raise httpx.ReadTimeout('fixture')

    async def notify(attempt, delay):
        retries.append(attempt)

    with pytest.raises(RuntimeError, match='5 次'):
        await retry_model_request(operation, notify)
    assert len(requests) == 6
    assert retries == [1, 2, 3, 4, 5]
    assert fast_backoff == [1, 2, 4, 8, 16]
