"""Protocol regressions through the real OpenAI SDK and synthetic HTTP streams."""
import json

import httpx
import pytest

from backend.config import Settings, SettingsStore
from backend.agent import CadAgent
from backend.knowledge import KnowledgeBase
from backend.storage import Database
from backend.model_probe import check_model
from backend.tool_protocol import AnswerFilter, PROTOCOL_NOTICE, has_tool_markup
from test_agent import FakeBridge


def stream_response(deltas, finish='stop'):
    rows = [dict(id='fixture', object='chat.completion.chunk', created=0, model='fixture',
                 choices=[dict(index=0, delta=delta, finish_reason=None)]) for delta in deltas]
    if finish:
        rows.append(dict(id='fixture', object='chat.completion.chunk', created=0, model='fixture',
                         choices=[dict(index=0, delta={}, finish_reason=finish)]))
    return httpx.Response(200, headers={'content-type': 'text/event-stream'},
        text=''.join('data: ' + json.dumps(row) + '\n\n' for row in rows) + 'data: [DONE]\n\n')


def call_deltas(name='connection_probe', arguments='{"value":"Dimensions define geometry."}', id='call_1'):
    return [{'tool_calls': [{'index': 0, 'id': id, 'type': 'function',
                            'function': {'name': name, 'arguments': arguments[:8]}}]},
            {'tool_calls': [{'index': 0, 'function': {'arguments': arguments[8:]}}]}]


def mock_client(monkeypatch, module, handler):
    monkeypatch.setattr(module, 'client_options', lambda config, **kw: {
        'base_url': 'https://fixture.test/v1', 'max_retries': 0,
        'http_client': httpx.AsyncClient(transport=httpx.MockTransport(handler))})


@pytest.mark.parametrize('marker', ['<|DSML|calls>', '< | | DSML | | invoke name="x">',
                                   '<｜DSML｜parameter', '</ | DSML | calls>', '<|DSML'])
def test_markup_filtered_at_every_fragment_boundary(marker):
    for split in range(len(marker) + 1):
        filter_ = AnswerFilter()
        visible = filter_.feed('Before ' + marker[:split]) + filter_.feed(marker[split:] + ' payload') + filter_.finish()
        assert visible == 'Before ' + PROTOCOL_NOTICE
        assert has_tool_markup(marker)


@pytest.mark.parametrize('text', ['2 < 3 and 4 > 1', '<b>normal text</b>', 'DSML is a protocol', 'normal prose'])
def test_non_protocol_text_preserved(text):
    filter_ = AnswerFilter()
    assert ''.join(filter_.feed(char) for char in text) + filter_.finish() == text


@pytest.mark.parametrize('outcome', ['ok', 'apology', 'truncated', 'wrong_tool', 'bad_json', 'missing_id', 'extra_arg', 'dsml'])
async def test_probe_validates_complete_call_and_result_roundtrip(monkeypatch, outcome):
    import backend.model_probe as module
    requests = []
    def handler(request):
        body = json.loads(request.content)
        requests.append(body)
        if len(requests) == 1:
            assert body['tool_choice'] == 'required'
            if outcome == 'dsml':
                return stream_response([{'content': '< | | DSML | | calls>'}])
            return stream_response(call_deltas(
                name='invented' if outcome == 'wrong_tool' else 'connection_probe',
                id='' if outcome == 'missing_id' else 'call_1',
                arguments='{' if outcome == 'bad_json' else ('{"value":"ok","extra":1}' if outcome == 'extra_arg' else '{"value":"ok"}')),
                finish='length' if outcome == 'truncated' else 'tool_calls')
        assert body['messages'][-1]['role'] == 'tool'
        assert body['messages'][-1]['tool_call_id'] == 'call_1'
        assert 'tools' not in body
        receipt = json.loads(body['messages'][-1]['content'])['receipt']
        return stream_response([{'content': receipt if outcome == 'ok' else 'I made a mistake.'}])
    mock_client(monkeypatch, module, handler)
    if outcome == 'truncated':
        with pytest.raises(ValueError, match='截断'):
            await check_model(config=Settings(), key='fixture')
        assert len(requests) == 1
        return
    result = await check_model(config=Settings(), key='fixture')
    assert result['tool_roundtrip_verified'] is (outcome == 'ok')
    assert result['tool_call_observed'] is (outcome in {'ok', 'apology'})
    assert len(requests) == (2 if outcome in {'ok', 'apology'} else 1)
    if outcome != 'ok':
        assert '未通过' in result['detail']


@pytest.mark.parametrize('repair', ['native', 'apology', 'invalid_args'])
async def test_sdk_recovery_requires_native_call_and_keeps_validation(tmp_path, monkeypatch, repair):
    import backend.agent as module
    monkeypatch.setenv('FORMA_API_KEY', 'fixture')
    db = Database(tmp_path / 'protocol.db')
    settings = SettingsStore(tmp_path / 'settings.json')
    bridge = FakeBridge()
    session = db.create_session()
    db.add_message(session['id'], 'user', 'Check live status')
    requests, events = [], []
    def handler(request):
        body = json.loads(request.content)
        requests.append(body)
        if len(requests) == 1:
            assert body['tool_choice'] == 'auto'
            return stream_response([{'content': '< | | DS'}, {'content': 'ML | | invoke name="sw_status">'}])
        if len(requests) == 2 or repair == 'apology':
            assert body['tool_choice'] == 'required'
            assert not any(has_tool_markup(m.get('content') or '') for m in body['messages'] if isinstance(m.get('content'), str))
            if repair == 'apology':
                return stream_response([{'content': 'I made a mistake; I will do it next time.'}])
            return stream_response(call_deltas(name='sw_status', arguments='{"bogus":1}' if repair == 'invalid_args' else '{}'), 'tool_calls')
        if repair == 'invalid_args' and len(requests) == 3:
            assert body['tool_choice'] == 'required'
            assert 'argument_schema' in body['messages'][-1]['content']
            return stream_response(call_deltas(name='sw_status', arguments='{}'), 'tool_calls')
        assert body['tool_choice'] == 'auto'
        assert body['messages'][-1]['role'] == 'tool'
        return stream_response([{'content': 'Status reviewed.'}])
    mock_client(monkeypatch, module, handler)
    async def emit(event):
        events.append(event)
    agent = CadAgent(db, settings, bridge, KnowledgeBase(db, settings))
    if repair == 'apology':
        with pytest.raises(RuntimeError, match='DSML'):
            await agent.run(session['id'], 'Check live status', emit)
        assert bridge.calls == [('sw_status', {})]
    else:
        await agent.run(session['id'], 'Check live status', emit)
        assert len(bridge.calls) == 3
    assert len(requests) == (4 if repair == 'invalid_args' else 3)
    assert all(not has_tool_markup(e.get('content', '')) for e in events if e['type'] in {'token', 'answer'})
    assert not has_tool_markup(db.messages(session['id'])[-1]['content'])
    if repair == 'invalid_args':
        assert not any(e['type'] == 'tool_result' and e['is_error'] for e in events)
        assert len([e for e in events if e['type'] == 'status' and '协议校验' in e.get('message', '')]) == 2
