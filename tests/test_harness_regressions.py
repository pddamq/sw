"""Recent real defect shapes, replayed offline through every native protocol."""
import json
from types import SimpleNamespace as NS

import pytest

from backend.agent import CadAgent
from backend.config import Settings, SettingsStore
from backend.execution import ExecutionControl
from backend.knowledge import KnowledgeBase
from backend.request_journal import RequestJournal
from backend.storage import Database
from backend.user_questions import QuestionAnswer
from test_agent import FakeBridge, FakeClient, chunk
from test_native_model_protocols import install_transport, sse, responses_rows, claude_rows


@pytest.mark.parametrize('protocol', ['chat', 'responses', 'claude'])
async def test_explanation_discovery_metadata_skip_and_continue_roundtrip(tmp_path, monkeypatch, protocol):
    import backend.agent as module
    monkeypatch.setenv('FORMA_API_KEY', 'fixture')
    db = Database(tmp_path / 'regressions.db')
    settings = SettingsStore(tmp_path / 'settings.json')
    settings.value = Settings(protocol=protocol, base_url='https://fixture.test')
    sid = db.create_session()['id']
    run = db.create_run(sid, 'Inspect without launching CAD')
    db.add_message(sid, 'user', 'Inspect without launching CAD')
    explanation = 'The option `sw_connect(launch=true)` would start CAD; this task only inspects status.'
    frames = [
        (explanation, 'discover_tools', {'query': 'sw_status', '_forma_purpose': 'Discover status inspection'}),
        ('', 'request_clarification', {'question': 'Add a report label?', 'reason': 'Optional naming', '_forma_purpose': 'Ask for a label'}),
        ('', 'sw_status', {'_forma_purpose': 'Continue independent inspection after skip'}),
        ('Inspection complete; no label was supplied.', None, None),
    ]
    requests, events = [], []
    if protocol == 'chat':
        responses = [[chunk(text, [NS(index=0, id=f'call-{i}', function=NS(name=name, arguments=json.dumps(args)))] if name else [])]
                     for i, (text, name, args) in enumerate(frames)]
        monkeypatch.setattr(module, 'AsyncOpenAI', lambda **kw: FakeClient(responses, requests))
    else:
        rows = responses_rows if protocol == 'responses' else claude_rows
        def handler(request):
            i = len(requests)
            requests.append(json.loads(request.content))
            text, name, args = frames[i]
            if name:
                result = rows(call={'call_id': f'call-{i}', 'name': name, 'arguments': json.dumps(args)})
                # Separate text block indices for Messages; Responses final output must include text too.
                if text and protocol == 'responses':
                    result.insert(0, {'type': 'response.output_text.delta', 'delta': text})
                    result[-1]['response']['output'].insert(0, {'type': 'message', 'role': 'assistant',
                        'content': [{'type': 'output_text', 'text': text}]})
                if text and protocol == 'claude':
                    result[1:1] = [
                        {'type': 'content_block_start', 'index': 1, 'content_block': {'type': 'text', 'text': ''}},
                        {'type': 'content_block_delta', 'index': 1, 'delta': {'type': 'text_delta', 'text': text}},
                        {'type': 'content_block_stop', 'index': 1}]
                return sse(result)
            return sse(rows(text=text))
        install_transport(monkeypatch, handler)
    bridge = FakeBridge()
    agent = CadAgent(db, settings, bridge, KnowledgeBase(db, settings))
    async def emit(event):
        events.append(event)
        db.event(sid, {**event, 'task_id': run['id']})
        if event['type'] == 'question_required':
            agent.control.answer_question(event['question']['id'], QuestionAnswer(cancelled=True))
    agent.control = ExecutionControl(db, bridge, run['id'], emit)
    await agent.run(sid, 'Inspect without launching CAD', emit)
    assert len(requests) == 4
    assert all(name == 'sw_status' and args == {} for name, args in bridge.calls)
    assert any(e['type'] == 'question_resolved' and e['outcome'] == 'skipped' for e in events)
    assert not any(e['type'] == 'approval_required' or e.get('phase') == 'rejected' for e in events)
    assert 'no label was supplied' in db.messages(sid)[-1]['content']
    records = RequestJournal(db).page(sid)['records']
    request_after_skip = RequestJournal(db).detail(sid, records[1]['id'])['snapshot']['request']
    result = next(m for m in request_after_skip['messages'] if m.get('tool_call_id') == 'call-1')
    assert json.loads(result['content'])['outcome'] == 'skipped'
    assert json.loads(result['content'])['text'] == ''


async def test_capture_storage_failure_stops_before_model_dispatch(tmp_path, monkeypatch):
    import backend.agent as module
    monkeypatch.setenv('FORMA_API_KEY', 'fixture')
    db = Database(tmp_path / 'fault.db')
    settings = SettingsStore(tmp_path / 'settings.json')
    sid = db.create_session()['id']
    db.add_message(sid, 'user', 'Inspect')
    captures = []
    monkeypatch.setattr(module, 'AsyncOpenAI', lambda **kw: FakeClient([[chunk('unused')]], captures))
    def broken(*args, **kwargs): raise OSError('storage unavailable')
    monkeypatch.setattr(RequestJournal, 'capture', broken)
    bridge = FakeBridge(); bridge.running = False
    async def emit(event): pass
    with pytest.raises(OSError, match='storage unavailable'):
        await CadAgent(db, settings, bridge, KnowledgeBase(db, settings)).run(sid, 'Inspect', emit)
    assert captures == [] and bridge.calls == []
