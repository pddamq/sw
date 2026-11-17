"""dsh-style output ceiling settlement and bounded usage collection."""
import asyncio
import json
from types import SimpleNamespace as NS

import httpx
import pytest

from backend.app import create_app
from backend.config import Settings, SettingsStore
from backend.model_retry import retrying_stream
from backend.output_limit import OutputLimitReached, reported_usage
from backend.storage import Database
from test_agent import FakeClient, FakeStream, chunk
from test_streaming_preview import setup


def terminal(reason='length', content=None, calls=None):
    item = chunk(content, calls)
    item.choices[0].finish_reason = reason
    return item


@pytest.mark.parametrize('with_usage', [False, True])
async def test_ceiling_skips_entire_batch_retains_prior_work_and_never_retries(tmp_path, monkeypatch, with_usage):
    import backend.agent as module
    agent, db, sid, bridge, events, emit = setup(tmp_path, monkeypatch)
    captures, traces = [], []
    agent.diagnostic = lambda kind, data: traces.append((kind, data))
    first = NS(index=0, id='completed', function=NS(name='sw_status', arguments='{}'))
    skipped = NS(index=0, id='skipped', function=NS(name='sw_status', arguments='{}'))
    broken = NS(index=1, id='broken', function=NS(name='sw_status', arguments='{'))
    truncated = [terminal(content='Incomplete reply', calls=[skipped, broken])]
    if with_usage:
        truncated.append(NS(choices=[], usage=NS(prompt_tokens=120, completion_tokens=45,
            total_tokens=165, completion_tokens_details={'reasoning_tokens': 40})))
    monkeypatch.setattr(module, 'AsyncOpenAI', lambda **kw: FakeClient([
        [chunk('Earlier completed reply', [first])], truncated,
    ], captures))
    with pytest.raises(OutputLimitReached) as error:
        await agent.run(sid, 'Inspect status', emit)
    assert error.value.limit == 256000
    assert len(captures) == 2
    assert all(c['max_tokens'] == 256000 for c in captures)
    assert captures[1]['stream_options'] == {'include_usage': True}
    assert captures[1]['messages'][-1]['role'] == 'tool'
    assert [e['call_id'] for e in events if e['type'] == 'tool_start' and e.get('call_id')] == ['completed']
    assert not any(e['type'] == 'model_retry' for e in events)
    assert db.messages(sid)[-1]['content'] == 'Earlier completed reply'
    limit = next(e for e in events if e['type'] == 'output_limit')
    assert limit['skipped_tool_calls'] == 2
    assert limit['usage'] == ({'prompt_tokens': 120, 'completion_tokens': 45,
                              'total_tokens': 165, 'reasoning_tokens': 40} if with_usage else {})
    assert next(e for e in events if e['type'] == 'answer_preview_end')['status'] == 'max_tokens'
    trace = [data for kind, data in traces if kind == 'model_response'][-1]
    assert trace['finish_reason'] == 'length' and trace['usage'] == limit['usage']


@pytest.mark.parametrize('reason', ['content_filter', 'incomplete'])
async def test_other_incomplete_reasons_are_not_output_ceiling(tmp_path, monkeypatch, reason):
    import backend.agent as module
    agent, db, sid, bridge, events, emit = setup(tmp_path, monkeypatch)
    captures = []
    monkeypatch.setattr(module, 'AsyncOpenAI', lambda **kw: FakeClient([[terminal(reason, 'Partial')]], captures))
    with pytest.raises(RuntimeError) as error:
        await agent.run(sid, 'Inspect', emit)
    assert not isinstance(error.value, OutputLimitReached)
    assert len(captures) == 1
    assert not any(e['type'] in {'output_limit', 'model_retry'} for e in events)


@pytest.mark.parametrize('tail_kind', ['usage', 'eof', 'hang', 'error', 'cancel'])
async def test_usage_tail_is_bounded_and_never_retries_or_admits_content(tail_kind):
    closed, retries = [], []

    class Stream(FakeStream):
        async def __aexit__(self, *args):
            closed.append(True)

        async def generate(self):
            yield terminal()
            yield chunk('Ignore post-terminal content')
            if tail_kind == 'usage':
                yield NS(choices=[], usage=NS(completion_tokens=23))
            elif tail_kind == 'hang':
                await asyncio.Event().wait()
            elif tail_kind == 'error':
                raise httpx.ReadError('tail disconnected')
            elif tail_kind == 'cancel':
                raise asyncio.CancelledError()

    async def create():
        return Stream([])

    async def notify(*args):
        retries.append(args)

    async def consume():
        async with retrying_stream(create, notify, usage_grace_seconds=0.02) as stream:
            return [item async for item in stream]

    if tail_kind == 'cancel':
        with pytest.raises(asyncio.CancelledError):
            await consume()
    else:
        result = await asyncio.wait_for(consume(), 1)
        assert len(result) == (2 if tail_kind == 'usage' else 1)
    assert closed == [True] and retries == []


async def test_reasoning_only_ceiling_is_visible_without_inventing_usage(tmp_path, monkeypatch):
    import backend.agent as module
    agent, db, sid, bridge, events, emit = setup(tmp_path, monkeypatch)
    thought = terminal()
    thought.choices[0].delta.reasoning_content = 'Incomplete analysis'
    monkeypatch.setattr(module, 'AsyncOpenAI', lambda **kw: FakeClient([[thought]], []))
    with pytest.raises(OutputLimitReached) as error:
        await agent.run(sid, 'Inspect', emit)
    assert error.value.usage == {}
    reasoning = next(e for e in events if e['type'] == 'reasoning_end')
    assert reasoning['status'] == 'max_tokens' and reasoning['content'] == 'Incomplete analysis'
    assert any(e['type'] == 'output_limit' for e in events)
    assert not any(e['type'] in {'answer', 'answer_preview_end'} for e in events)


async def test_retry_clears_discarded_attempt_usage(tmp_path, monkeypatch):
    import backend.agent as module
    import backend.model_retry as retry
    agent, db, sid, bridge, events, emit = setup(tmp_path, monkeypatch)
    captures = []

    class Broken(FakeStream):
        async def generate(self):
            yield NS(choices=[], usage=NS(completion_tokens=900))
            raise httpx.ReadError('Interrupted before finish')

    class Client(FakeClient):
        async def create(self, **kwargs):
            captures.append(kwargs)
            return Broken([]) if len(captures) == 1 else FakeStream([terminal()])

    async def wait(delay):
        pass

    monkeypatch.setattr(retry, 'wait_before_retry', wait)
    monkeypatch.setattr(module, 'AsyncOpenAI', lambda **kw: Client([], captures))
    with pytest.raises(OutputLimitReached) as error:
        await agent.run(sid, 'Inspect', emit)
    assert error.value.usage == {}
    assert len(captures) == 2
    assert sum(e['type'] == 'model_retry' for e in events) == 1


def test_usage_is_reported_not_inferred():
    assert reported_usage(NS(prompt_tokens=True, completion_tokens=-1, total_tokens='12',
                             completion_tokens_details={'reasoning_tokens': 2.5})) == {}
    assert reported_usage(NS(completion_tokens=0, completion_tokens_details=NS(reasoning_tokens=0))) == {
        'completion_tokens': 0, 'reasoning_tokens': 0}
    assert OutputLimitReached(256000).usage == {}


async def test_api_persists_ceiling_and_preview_releases_lock_and_allows_next_request(tmp_path, monkeypatch):
    monkeypatch.setenv('FORMA_API_KEY', 'fixture-key')
    db = Database(tmp_path / 'api.db')
    app = create_app(db, SettingsStore(tmp_path / 'settings.json'), token='fixture')

    async def run(session_id, query, emit):
        await emit({'type': 'answer_preview_end', 'round': 1, 'content': 'Partial', 'status': 'max_tokens'})
        raise OutputLimitReached(256000, {'completion_tokens': 43})

    monkeypatch.setattr(app.state.agent, 'run', run)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url='http://testserver',
                                headers={'Authorization': 'Bearer fixture'}) as client:
        sid = (await client.post('/api/sessions', json={})).json()['id']
        response = await client.post('/api/chat', json={'session_id': sid, 'message': 'Inspect'})
        events = [json.loads(line[6:]) for line in response.text.splitlines() if line.startswith('data: ')]
        done = events[-1]
        assert done['type'] == 'done' and done['status'] == 'max_tokens'
        task = db.run(done['task']['id'])
        assert task['status'] == 'max_tokens' and task['finished_at']
        assert not any(e['type'] == 'error' for e in events)
        assert any(row['event']['type'] == 'answer_preview_end' for row in db.events(sid))
        assert not (await client.get('/api/health')).json()['task_running']
        assert app.state.agent.inbox is None and app.state.agent.control is None
        following = await client.post('/api/chat', json={'session_id': sid, 'message': 'Continue'})
        assert following.status_code == 200


@pytest.mark.parametrize('protocol,reason,expected', [
    ('responses', 'max_output_tokens', 'length'),
    ('responses', 'content_filter', 'content_filter'),
    ('responses', 'unknown_reason', 'incomplete'),
    ('claude', 'max_tokens', 'length'),
    ('claude', 'model_context_window_exceeded', 'incomplete'),
    ('claude', 'unknown_reason', 'incomplete'),
])
async def test_native_finish_reasons_remain_distinct(monkeypatch, protocol, reason, expected):
    from test_native_model_protocols import claude_rows, install_transport, native_client, sse
    if protocol == 'responses':
        rows = [{'type': 'response.incomplete', 'response': {'status': 'incomplete', 'output': [],
                 'incomplete_details': {'reason': reason},
                 'usage': {'input_tokens': 10, 'output_tokens': 20, 'output_tokens_details': {'reasoning_tokens': 15}}}}]
    else:
        rows = claude_rows(text='Partial')
        rows[-2]['delta']['stop_reason'] = reason
    install_transport(monkeypatch, lambda request: sse(rows))
    async with native_client(Settings(protocol=protocol, base_url='https://provider.test')) as client:
        stream = await client.create(model='fixture', messages=[{'role': 'user', 'content': 'Hi'}],
                                     max_tokens=256000, stream=True)
        async with stream:
            items = [item async for item in stream]
    assert items[-1].choices[0].finish_reason == expected
    if protocol == 'responses':
        assert reported_usage(items[-1].usage)['reasoning_tokens'] == 15
