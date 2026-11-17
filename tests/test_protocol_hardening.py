from types import SimpleNamespace as NS
import json

import httpx
import pytest

from backend.agent import CadAgent
from backend.config import Settings, SettingsStore
from backend.knowledge import KnowledgeBase
from backend.model_probe import check_model
from backend.storage import Database
from backend.tool_protocol import AnswerFilter, MAX_PENDING, HISTORY_NOTICE, assistant_history_text
from test_agent import FakeBridge, FakeClient, chunk
from test_protocol_recovery_sdk import call_deltas, mock_client, stream_response


def test_ordinary_angle_brackets_do_not_delay_stream():
    filter_ = AnswerFilter()
    assert filter_.feed('2 < 3; <b>text</b>') == '2 < 3; <b>text</b>'
    assert filter_.pending == ''
    assert filter_.feed(' tail') == ' tail'


@pytest.mark.parametrize('text', ['<|DSMLParser>example', '< | DSMLibrary is a name', '<|DScan>'])
def test_similar_names_are_not_protocol(text):
    for split in range(len(text) + 1):
        filter_ = AnswerFilter()
        assert filter_.feed(text[:split]) + filter_.feed(text[split:]) + filter_.finish() == text
        assert not filter_.blocked


def test_prefix_buffer_has_limit():
    filter_ = AnswerFilter()
    with pytest.raises(ValueError, match='前缀'):
        for _ in range(MAX_PENDING + 1):
            filter_.feed('<|' if not filter_.pending else ' ')


def test_history_sanitization_does_not_touch_plain_text():
    assert assistant_history_text('Measurements: 3 mm') == 'Measurements: 3 mm'
    text = assistant_history_text('Unverified plan: <|DSML|calls>payload')
    assert text.startswith(HISTORY_NOTICE)
    assert 'payload' not in text


async def test_old_assistant_markup_removed_only_from_model_input(tmp_path, monkeypatch):
    import backend.agent as module
    monkeypatch.setenv('FORMA_API_KEY', 'fixture')
    db = Database(tmp_path / 'history.db')
    settings = SettingsStore(tmp_path / 'settings.json')
    session = db.create_session()
    old = '< | | DSML | | invoke name="fake_tool">old payload'
    db.add_message(session['id'], 'assistant', old)
    db.add_message(session['id'], 'user', 'Explain this syntax literally: ' + old)
    captures = []
    monkeypatch.setattr(module, 'AsyncOpenAI', lambda **kw: FakeClient([[chunk('Explanation only.')]], captures))
    bridge = FakeBridge()
    bridge.running = False
    async def emit(event):
        pass
    await CadAgent(db, settings, bridge, KnowledgeBase(db, settings)).run(session['id'], 'Explain syntax', emit)
    assert next(m for m in captures[0]['messages'] if m['role'] == 'assistant')['content'].startswith(HISTORY_NOTICE)
    assert old in next(m for m in captures[0]['messages'] if m['role'] == 'user')['content']
    assert db.messages(session['id'])[0]['content'] == old


async def test_repair_omits_invalid_reasoning_and_identifies_application_feedback(tmp_path, monkeypatch):
    import backend.agent as module
    monkeypatch.setenv('FORMA_API_KEY', 'fixture')
    db = Database(tmp_path / 'reasoning.db')
    settings = SettingsStore(tmp_path / 'settings.json')
    session = db.create_session()
    db.add_message(session['id'], 'user', 'Check status')
    first = chunk('<|DSML|calls>')
    first.choices[0].delta.reasoning_content = 'Need to inspect status.'
    call = NS(index=0, id='c1', function=NS(name='sw_status', arguments='{}'))
    captures = []
    monkeypatch.setattr(module, 'AsyncOpenAI', lambda **kw: FakeClient([
        [first], [chunk(calls=[call])], [chunk('Status checked.')]], captures))
    async def emit(event):
        pass
    await CadAgent(db, settings, FakeBridge(), KnowledgeBase(db, settings)).run(session['id'], 'Check status', emit)
    assert not any(m['role'] == 'assistant' for m in captures[1]['messages'])
    assert 'Need to inspect status.' not in json.dumps(captures[1]['messages'])
    assert 'NOT a user message' in captures[1]['messages'][-1]['content']
    assert captures[1]['tool_choice'] == 'required'


@pytest.mark.parametrize('status', [400, 422])
async def test_required_rejection_stops_without_silent_fallback(tmp_path, monkeypatch, status):
    import backend.agent as module
    monkeypatch.setenv('FORMA_API_KEY', 'fixture')
    requests = []
    def handler(request):
        requests.append(json.loads(request.content))
        if len(requests) == 1:
            return stream_response([{'content': '<|DSML|calls>'}])
        return httpx.Response(status, json={'error': {'message': 'unsupported tool_choice'}})
    mock_client(monkeypatch, module, handler)
    db = Database(tmp_path / 'required.db')
    settings = SettingsStore(tmp_path / 'settings.json')
    session = db.create_session()
    db.add_message(session['id'], 'user', 'Check status')
    bridge = FakeBridge()
    async def emit(event):
        pass
    with pytest.raises(ValueError, match='tool_choice'):
        await CadAgent(db, settings, bridge, KnowledgeBase(db, settings)).run(session['id'], 'Check status', emit)
    assert len(requests) == 2
    assert bridge.calls == [('sw_status', {})]


@pytest.mark.parametrize('failure', ['http', 'connection', 'truncated', 'tool_calls'])
async def test_probe_second_leg_failure_is_not_success(monkeypatch, failure):
    import backend.model_probe as module
    requests = []
    def handler(request):
        requests.append(json.loads(request.content))
        if len(requests) == 1:
            return stream_response(call_deltas(), 'tool_calls')
        if failure == 'http':
            return httpx.Response(503, json={'error': {'message': 'sensitive provider details'}})
        if failure == 'connection':
            raise httpx.ConnectError('sensitive provider details')
        receipt = json.loads(requests[-1]['messages'][-1]['content'])['receipt']
        return stream_response([{'content': receipt}], 'length' if failure == 'truncated' else 'tool_calls')
    mock_client(monkeypatch, module, handler)
    result = await check_model(config=Settings(), key='fixture')
    assert result['tool_call_observed']
    assert not result['tool_roundtrip_verified']
    assert 'sensitive' not in result['detail']


async def test_probe_bounds_reasoning_output(monkeypatch):
    import backend.model_probe as module
    mock_client(monkeypatch, module, lambda request: stream_response([{'reasoning_content': 'x' * 64001}]))
    with pytest.raises(ValueError, match='大小限制'):
        await check_model(config=Settings(), key='fixture')


@pytest.mark.parametrize('status', [400, 422])
async def test_probe_usage_fallback_still_checks_required_rejection(monkeypatch, status):
    import backend.model_probe as module
    requests = []
    def handler(request):
        body = json.loads(request.content)
        requests.append(body)
        message = 'Unsupported stream_options' if len(requests) == 1 else 'Unsupported tool_choice'
        return httpx.Response(status, json={'error': {'message': message}})
    mock_client(monkeypatch, module, handler)
    with pytest.raises(ValueError, match='tool_choice=required'):
        await check_model(config=Settings(), key='fixture')
    assert len(requests) == 2
    assert 'stream_options' not in requests[1]
    assert all(body['tool_choice'] == 'required' for body in requests)
