"""Local question lifecycle, validation, API binding and agent continuation."""
import asyncio
import json
from types import SimpleNamespace as NS

import httpx
import pytest
from pydantic import ValidationError

from backend.agent import CadAgent
from backend.app import create_app
from backend.config import SettingsStore
from backend.execution import DecisionStopped, ExecutionControl
from backend.knowledge import KnowledgeBase
from backend.storage import Database
from backend.user_questions import QuestionAnswer, validate_answer, validate_question
from test_agent import FakeBridge, FakeClient, chunk

QUESTION = {'question': 'Which material and thickness?', 'reason': 'Required for the case design.',
            'options': [{'id': 'steel', 'label': 'Steel'}, {'id': 'aluminum', 'label': 'Aluminum'}]}


@pytest.mark.parametrize('payload', [
    {}, {'selected_option_ids': ['missing']}, {'selected_option_ids': ['steel', 'steel']},
    {'selected_option_ids': ['steel', 'aluminum']}, {'text': '   '},
    {'cancelled': True, 'text': 'still answer'},
])
def test_answers_reject_empty_unknown_duplicate_or_wrong_cardinality(payload):
    with pytest.raises(ValueError):
        validate_answer(QUESTION, QuestionAnswer(**payload))


@pytest.mark.parametrize('payload', [
    {'text': 1}, {'cancelled': 'true'}, {'selected_option_ids': [1]},
    {'text': 'x' * 4001}, {'approved': True},
])
def test_answer_transport_is_strict(payload):
    with pytest.raises(ValidationError):
        QuestionAnswer(**payload)


def test_valid_answer_supports_free_text_multiple_and_cancel():
    result = validate_answer(QUESTION, QuestionAnswer(text='  2 mm steel  '))
    assert result['text'] == '2 mm steel'
    result = validate_answer({**QUESTION, 'allow_multiple': True},
                             QuestionAnswer(selected_option_ids=['steel', 'aluminum'], text='Compare both'))
    assert [option['label'] for option in result['selected_options']] == ['Steel', 'Aluminum']
    assert validate_answer(QUESTION, QuestionAnswer(cancelled=True))['cancelled']


def test_question_rejects_duplicate_ids():
    with pytest.raises(ValueError, match='unique'):
        validate_question({**QUESTION, 'options': [QUESTION['options'][0]] * 2})


async def test_control_wait_answer_duplicate_and_cleanup(tmp_path):
    db = Database(tmp_path / 'ask.db')
    sid = db.create_session()['id']
    run = db.create_run(sid, 'Design')
    events, ready = [], asyncio.Event()
    async def emit(event):
        events.append(event)
        if event['type'] == 'question_required':
            ready.set()
    control = ExecutionControl(db, FakeBridge(), run['id'], emit)
    task = asyncio.create_task(control.ask_question(QUESTION))
    await asyncio.wait_for(ready.wait(), 2)
    assert db.run(run['id'])['status'] == 'waiting_input'
    assert db.run(run['id'])['finished_at'] is None
    assert not task.done() and control.bridge.calls == []
    question = control.pending_question
    assert question['session_id'] == sid
    with pytest.raises(ValueError):
        control.answer_question('stale', QuestionAnswer(text='bad'))
    control.answer_question(question['id'], QuestionAnswer(selected_option_ids=['steel'], text='2 mm'))
    with pytest.raises(ValueError):
        control.answer_question(question['id'], QuestionAnswer(text='duplicate'))
    result = await task
    assert result['selected_options'] == [{'id': 'steel', 'label': 'Steel'}]
    assert 'not CAD approval' in result['note']
    assert events[-1]['outcome'] == 'answered'
    assert control.pending_question is None and control.question_future is None
    assert db.run(run['id'])['status'] == 'running'
    assert control.pending is None and control.bridge.calls == []


@pytest.mark.parametrize('mode', ['cancel_task', 'timeout'])
async def test_question_stop_paths_never_resume_tools(tmp_path, mode):
    db = Database(tmp_path / 'ask.db')
    run = db.create_run(db.create_session()['id'], 'Design')
    events, ready = [], asyncio.Event()
    async def emit(event):
        events.append(event)
        ready.set()
    control = ExecutionControl(db, FakeBridge(), run['id'], emit)
    if mode == 'timeout':
        control.question_timeout = 0
    task = asyncio.create_task(control.ask_question(QUESTION))
    await asyncio.wait_for(ready.wait(), 2)
    if mode == 'cancel_task':
        db.update_run(run['id'], 'cancelling')
        task.cancel()
    with pytest.raises(asyncio.CancelledError if mode == 'cancel_task' else DecisionStopped):
        await task
    assert control.pending_question is None and control.question_future is None
    assert events[-1]['outcome'] == ('expired' if mode == 'timeout' else 'cancelled')
    assert control.bridge.calls == []
    if mode == 'cancel_task':
        assert db.run(run['id'])['status'] == 'cancelling'


@pytest.mark.parametrize('skip', [False, True])
async def test_local_question_available_without_cad_and_answer_returns_to_model(tmp_path, monkeypatch, skip):
    import backend.agent as module
    monkeypatch.setenv('FORMA_API_KEY', 'fixture')
    db = Database(tmp_path / 'agent.db')
    settings = SettingsStore(tmp_path / 'settings.json')
    sid = db.create_session()['id']
    run = db.create_run(sid, 'Design a case')
    bridge = FakeBridge()
    bridge.running = False
    call = NS(index=0, id='question_call', function=NS(name='request_clarification', arguments=json.dumps(QUESTION)))
    captures, events = [], []
    monkeypatch.setattr(module, 'AsyncOpenAI', lambda **kw: FakeClient([
        [chunk(calls=[call])], [chunk('The requested thickness is 2 mm.')]], captures))
    agent = CadAgent(db, settings, bridge, KnowledgeBase(db, settings))
    async def emit(event):
        events.append(event)
        db.event(sid, {**event, 'task_id': run['id']})
        if event['type'] == 'question_required':
            agent.control.answer_question(event['question']['id'],
                QuestionAnswer(cancelled=True) if skip else QuestionAnswer(text='2 mm'))
    agent.control = ExecutionControl(db, bridge, run['id'], emit)
    await agent.run(sid, 'Design a case', emit)
    assert [tool['function']['name'] for tool in captures[0]['tools']] == ['request_clarification']
    assert captures[1]['messages'][-1]['role'] == 'tool'
    result = json.loads(captures[1]['messages'][-1]['content'])
    assert result['text'] == ('' if skip else '2 mm')
    assert result['cancelled'] is skip
    assert result['outcome'] == ('skipped' if skip else 'answered')
    assert 'not CAD approval' in result['note']
    if skip:
        assert result['selected_options'] == []
        assert 'do not invent missing information or repeat the same question' in result['note']
    assert next(e for e in events if e['type'] == 'question_resolved')['outcome'] == result['outcome']
    assert agent.control.pending_question is None
    assert db.run(run['id'])['status'] == 'running'
    assert bridge.calls == []
    assert not any(event['type'] == 'approval_required' for event in events)
    assert db.messages(sid)[-1]['content'] == 'The requested thickness is 2 mm.'
    assert any(row['event']['type'] == 'question_resolved' for row in db.events(sid))


@pytest.mark.parametrize('cancel', [False, True])
async def test_question_api_auth_task_binding_health_and_completion(tmp_path, monkeypatch, cancel):
    monkeypatch.setenv('FORMA_API_KEY', 'fixture')
    db = Database(tmp_path / 'api.db')
    settings = SettingsStore(tmp_path / 'settings.json')
    bridge = FakeBridge()
    bridge.detail = 'fixture'
    app = create_app(db, settings, bridge, token='local')
    sid = db.create_session()['id']
    ready, proceeded = asyncio.Event(), []
    async def run(session_id, query, emit):
        original_emit = app.state.agent.control.emit
        async def record(event):
            await original_emit(event)
            if event['type'] == 'question_required':
                ready.set()
        app.state.agent.control.emit = record
        result = await app.state.agent.control.ask_question(QUESTION)
        proceeded.append(result)
    monkeypatch.setattr(app.state.agent, 'run', run)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url='http://testserver') as api:
        api.headers['Authorization'] = 'Bearer local'
        chat = asyncio.create_task(api.post('/api/chat', json={'session_id': sid, 'message': 'Design'}))
        try:
            await asyncio.wait_for(ready.wait(), 3)
            health = (await api.get('/api/health')).json()
            question = health['pending_question']
            task_id = health['active_task']['id']
            assert health['active_task']['status'] == 'waiting_input'
            assert health['pending_approval'] is None
            assert not proceeded
            endpoint = f'/api/tasks/{task_id}/questions/{question["id"]}'
            assert (await api.post(endpoint, json={'text': '2 mm'}, headers={'Authorization': ''})).status_code == 401
            assert (await api.post('/api/tasks/wrong/questions/' + question['id'], json={'text': '2 mm'})).status_code == 409
            assert (await api.post(f'/api/tasks/{task_id}/questions/stale', json={'text': '2 mm'})).status_code == 409
            assert (await api.post(endpoint, json={'text': ''})).status_code == 409
            assert (await api.post(endpoint, json={'cancelled': 'yes'})).status_code == 422
            assert (await api.post(endpoint, json={'cancelled': True} if cancel else {'text': '2 mm'})).status_code == 200
            await asyncio.wait_for(chat, 3)
            assert (await api.post(endpoint, json={'text': 'again'})).status_code == 409
            assert (await api.get('/api/health')).json()['pending_question'] is None
            assert len(proceeded) == 1
            assert proceeded[0]['cancelled'] is cancel
            assert proceeded[0]['outcome'] == ('skipped' if cancel else 'answered')
            assert db.run(task_id)['status'] == 'completed'
            assert any(r['event']['type'] == 'question_resolved' for r in db.events(sid))
        finally:
            if not chat.done():
                await api.post('/api/tasks/cancel')
                await asyncio.wait_for(chat, 3)


def test_dead_process_waiting_input_is_recovered(tmp_path, monkeypatch):
    import backend.runtime as runtime
    db = Database(tmp_path / 'recovery.db')
    run = db.create_run(db.create_session()['id'], 'Design')
    db.update_run(run['id'], 'waiting_input')
    monkeypatch.setattr(runtime, 'process_alive', lambda pid: False)
    db.recover_interrupted_runs()
    assert db.run(run['id'])['status'] == 'interrupted'
