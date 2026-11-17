"""Engineering checks use synthetic CAD fixtures, never real SolidWorks mutations."""
import asyncio
import copy
import json
from types import SimpleNamespace as NS

import httpx
import pytest

from backend.agent import CadAgent
from backend.app import create_app
from backend.config import SettingsStore
from backend.engineering import EngineeringWorkflow, EngineeringStore, EngineeringReviewRequired, evaluate
from backend.execution import ExecutionControl, ExecutionHalted, build_report
from backend.knowledge import KnowledgeBase
from backend.storage import Database
from test_agent import FakeClient, chunk


PLAN = {'target_document': 'FixturePart', 'process': 'concept', 'material': 'Steel', 'requirements': [
    {'id': 'bodies', 'description': 'Exactly one solid', 'checker': 'solid_body_count', 'expected': '1', 'source': 'User requirement'},
    {'id': 'material', 'description': 'Material', 'checker': 'material_equals', 'expected': 'Steel', 'source': 'User requirement'},
]}


class EngineeringBridge:
    detail = 'Synthetic engineering fixture'
    running = True
    tools = [{'name': name, 'description': name, 'annotations': {'readOnlyHint': name != 'feature_extrude', 'destructiveHint': False},
              'inputSchema': {'type': 'object', 'properties': {} if name == 'sw_status' else {'doc': {'type': 'string'}},
                              'additionalProperties': False}}
             for name in ('sw_status', 'model_info', 'model_review', 'model_check', 'feature_extrude')]

    def __init__(self):
        self.calls = []
        self.stamp = 1
        self.errors = 0
        self.change_during_check = False
        self.complete = True
        self.stamps = True
        self.fail = ''
        self.material = 'Steel'
        self.count = 1
        self.sw_status = self.status()

    def status(self):
        doc = {'title': 'FixturePart', 'handle': 'D1', 'configuration': 'Default', 'type': 'part', 'target': True}
        if self.stamps:
            doc['updateStamp'] = self.stamp
        return {'attached': True, 'documents': [doc], 'targetDocument': 'FixturePart', 'solidWorksPid': 1}

    async def call(self, name, arguments):
        self.calls.append((name, arguments))
        if name == self.fail:
            raise RuntimeError('Transport lost')
        if name == 'feature_extrude':
            self.errors = 0
            self.stamp += 1
            data = {'doc': 'FixturePart'}
        elif name == 'sw_status':
            data = self.status()
        elif name == 'model_info':
            data = {'doc': 'FixturePart', 'type': 'part', 'material': self.material,
                    'bodies': [{'type': 'solid'} for _ in range(self.count)]}
        elif name == 'model_review':
            data = {'doc': 'FixturePart', 'type': 'part', 'errorCount': self.errors, 'warningCount': 0,
                    'issues': [{'severity': 'error', 'kind': 'rebuild_error'}] if self.errors else [], 'complete': self.complete}
        elif name == 'model_check':
            data = {'doc': 'FixturePart', 'valid': True, 'faultCount': 0,
                    'bodies': [{'valid': True, 'faults': []}]}
            if self.change_during_check:
                self.stamp += 1
        else:
            raise AssertionError(name)
        return {'is_error': False, 'content': json.dumps(data)}


def setup(tmp_path):
    db = Database(tmp_path / 'engineering.db')
    session = db.create_session()
    task = db.create_run(session['id'], 'Create fixture part')
    events = []
    async def emit(event):
        events.append(event)
    bridge = EngineeringBridge()
    control = ExecutionControl(db, bridge, task['id'], emit)
    workflow = EngineeringWorkflow(control)
    control.engineering = workflow
    return db, session, task, bridge, control, workflow, events, emit


async def confirm(workflow, control, answer=None):
    async def ask(arguments):
        assert 'FixturePart' in arguments['question']
        return answer or {'cancelled': False, 'selected_option_ids': ['accept'], 'text': ''}
    control.ask_question = ask
    return await workflow.propose(copy.deepcopy(PLAN))


async def test_confirmation_freezes_contract_and_persists(tmp_path):
    db, _, task, _, control, workflow, _, _ = setup(tmp_path)
    with pytest.raises(ValueError, match='engineering_plan_required'):
        await workflow.before_operation('feature_extrude')
    await confirm(workflow, control)
    first_hash = workflow.plan_hash
    with pytest.raises(ValueError, match='冻结'):
        await workflow.propose({**PLAN, 'material': 'Copper'})
    report = await workflow.check()
    assert report['disposition'] == 'checks_passed'
    assert report['production_released'] is False
    assert report['plan_hash'] == first_hash
    assert EngineeringStore(Database(db.path)).latest(task['id'])['id'] == report['id']
    assert build_report(db, task['id'])['engineering']['id'] == report['id']
    assert build_report(db, task['id'])['engineering_verified'] is False
    assert {row['name'] for row in db.checkpoints(task['id'])} >= {'model_info', 'model_review', 'model_check'}


@pytest.mark.parametrize('answer', [
    {'cancelled': True, 'selected_option_ids': [], 'text': ''},
    {'cancelled': False, 'selected_option_ids': ['revise'], 'text': 'change thickness'},
    {'cancelled': False, 'selected_option_ids': ['accept'], 'text': 'but change material'},
])
async def test_skipped_or_amended_confirmation_does_not_enable_edits(tmp_path, answer):
    *_, control, workflow, events, emit = setup(tmp_path)
    result = await confirm(workflow, control, answer)
    assert not result['confirmed'] and workflow.plan is None
    with pytest.raises(ValueError):
        await workflow.before_operation('feature_extrude')


async def test_report_invalidated_before_mutation_and_after_steering(tmp_path):
    db, _, task, bridge, control, workflow, _, _ = setup(tmp_path)
    await confirm(workflow, control)
    await workflow.check()
    await workflow.before_operation('feature_extrude', {'doc': 'D1'})
    assert EngineeringStore(db).latest(task['id'])['disposition'] == 'stale'
    await workflow.invalidate('new user input', reconfirm=True)
    with pytest.raises(ValueError, match='engineering_plan_required'):
        await workflow.before_operation('feature_extrude')
    assert (await workflow.check())['disposition'] == 'needs_review'
    await confirm(workflow, control)
    assert workflow.plan and not workflow.reconfirm
    with pytest.raises(ValueError, match='范围'):
        await workflow.before_operation('feature_extrude', {'doc': 'AnotherPart'})


@pytest.mark.parametrize('mode', ['incomplete', 'changing', 'legacy', 'missing_tool', 'material_missing'])
async def test_unknown_never_passes_or_blindly_repairs(tmp_path, mode):
    _, _, _, bridge, control, workflow, _, _ = setup(tmp_path)
    await confirm(workflow, control)
    if mode == 'incomplete': bridge.complete = False
    if mode == 'changing': bridge.change_during_check = True
    if mode == 'legacy': bridge.stamps = False
    if mode == 'missing_tool': bridge.tools = [t for t in bridge.tools if t['name'] != 'model_check']
    if mode == 'material_missing': bridge.material = None
    with pytest.raises(EngineeringReviewRequired):
        await workflow.finish()
    assert any(item['status'] == 'UNKNOWN' for item in workflow.report['items'])
    assert workflow.repair_attempts == 0
    assert workflow.report['production_released'] is False


async def test_defect_repair_and_recheck(tmp_path):
    _, _, _, bridge, control, workflow, _, _ = setup(tmp_path)
    await confirm(workflow, control)
    bridge.errors = 1
    feedback = await workflow.finish()
    assert 'FAIL' in feedback and workflow.repair_attempts == 1
    await workflow.before_operation('feature_extrude')
    await control.invoke('feature_extrude', {})
    assert await workflow.finish() is None
    assert workflow.report['disposition'] == 'checks_passed'
    assert workflow.report['mutation_epoch'] == 1


async def test_mixed_engineering_batch_executes_no_cad_or_confirmation(tmp_path, monkeypatch):
    import backend.agent as module
    db, session, _, bridge, control, workflow, events, emit = setup(tmp_path)
    config = SettingsStore(tmp_path / 'settings.json')
    monkeypatch.setenv('FORMA_API_KEY', 'fixture')
    mutation = tool('feature_extrude', {}, 'mixed-edit')
    proposal = tool('engineering_plan', PLAN, 'mixed-plan')
    proposal.index = 1
    monkeypatch.setattr(module, 'AsyncOpenAI', lambda **kw: FakeClient([
        [chunk(calls=[mutation, proposal])], [chunk('Pending confirmation.')],
    ], []))
    agent = CadAgent(db, config, bridge, KnowledgeBase(db, config))
    agent.control = control
    with pytest.raises(EngineeringReviewRequired):
        await agent.run(session['id'], 'feature_extrude fixture', emit)
    assert workflow.proposals == 0
    assert not any(name == 'feature_extrude' for name, _ in bridge.calls)
    results = [e for e in events if e['type'] == 'tool_result' and e.get('call_id') in ('mixed-edit', 'mixed-plan')]
    assert len(results) == 2
    assert all(e['outcome'] == 'not_executed' for e in results)


async def test_known_failures_repair_even_when_manufacturing_is_unknown(tmp_path):
    _, _, _, bridge, control, workflow, _, _ = setup(tmp_path)
    async def ask(_): return {'cancelled': False, 'selected_option_ids': ['accept'], 'text': ''}
    control.ask_question = ask
    await workflow.propose({**PLAN, 'process': 'machining'})
    bridge.errors = 1
    assert await workflow.finish()
    bridge.errors = 0
    with pytest.raises(EngineeringReviewRequired):
        await workflow.finish()
    assert workflow.report['disposition'] == 'needs_review'
    assert next(i for i in workflow.report['items'] if i['id'] == '_manufacturing')['status'] == 'UNKNOWN'


async def test_no_progress_stops_without_weakening_requirements(tmp_path):
    _, _, _, bridge, control, workflow, _, _ = setup(tmp_path)
    await confirm(workflow, control)
    bridge.errors = 1
    await workflow.finish()
    with pytest.raises(EngineeringReviewRequired, match='缺陷未减少'):
        await workflow.finish()
    assert workflow.plan == PLAN and workflow.repair_attempts == 1


async def test_repair_and_check_budgets_are_separate_from_normal_rounds(tmp_path):
    _, _, _, bridge, control, workflow, _, _ = setup(tmp_path)
    for _ in range(100): workflow.model_step()
    await confirm(workflow, control)
    bridge.errors = 1
    await workflow.finish()
    for _ in range(workflow.MAX_REPAIR_ROUNDS): workflow.model_step()
    with pytest.raises(EngineeringReviewRequired, match='修复多轮'):
        workflow.model_step()
    workflow.checks = workflow.MAX_CHECKS
    with pytest.raises(EngineeringReviewRequired, match='检查次数'):
        await workflow.check()


async def test_executor_failure_stops_and_retains_unknown_checkpoint(tmp_path):
    db, _, task, bridge, control, workflow, _, _ = setup(tmp_path)
    await confirm(workflow, control)
    bridge.fail = 'model_review'
    with pytest.raises(ExecutionHalted):
        await workflow.check()
    assert db.checkpoints(task['id'])[-1]['status'] == 'unknown'
    await workflow.interrupt('disconnected')
    assert workflow.report['disposition'] == 'interrupted'
    assert not any(name == 'model_check' for name, _ in bridge.calls)


async def test_cancelled_check_never_leaves_pass_report(tmp_path):
    db, _, task, bridge, control, workflow, _, _ = setup(tmp_path)
    await confirm(workflow, control)
    await workflow.check()
    original = bridge.call
    async def cancelled(name, args):
        if name == 'model_review': raise asyncio.CancelledError()
        return await original(name, args)
    bridge.call = cancelled
    with pytest.raises(asyncio.CancelledError):
        await workflow.check()
    assert workflow.report['disposition'] == 'stale'
    await workflow.interrupt('cancelled')
    assert workflow.report['disposition'] == 'interrupted'
    assert db.checkpoints(task['id'])[-1]['status'] == 'unknown'


def test_empty_and_malformed_geometry_are_not_pass():
    for check in ({}, {'doc': 'FixturePart', 'valid': True, 'faultCount': 0, 'bodies': []},
                  {'doc': 'FixturePart', 'valid': 'true', 'faultCount': 0, 'bodies': [{}]}):
        items = evaluate(PLAN, {'model_info': {'doc': 'FixturePart', 'type': 'part'}, 'model_check': check})
        assert next(i for i in items if i['id'] == '_geometry')['status'] == 'UNKNOWN'


def tool(name, args, id_):
    return NS(index=0, id=id_, function=NS(name=name, arguments=json.dumps(args)))


async def test_real_agent_loop_checks_repairs_and_rechecks(tmp_path, monkeypatch):
    import backend.agent as module
    db, session, _, bridge, control, workflow, events, emit = setup(tmp_path)
    await confirm(workflow, control)
    bridge.errors = 1
    config = SettingsStore(tmp_path / 'settings.json')
    monkeypatch.setenv('FORMA_API_KEY', 'fixture')
    captures = []
    monkeypatch.setattr(module, 'AsyncOpenAI', lambda **kw: FakeClient([
        [chunk('Model ready.')],
        [chunk(calls=[tool('feature_extrude', {}, 'repair')])],
        [chunk('Repair done; review evidence.')],
    ], captures))
    agent = CadAgent(db, config, bridge, KnowledgeBase(db, config))
    agent.control = control
    await agent.run(session['id'], 'feature_extrude fixture', emit)
    assert len(captures) == 3
    assert 'APPLICATION ENGINEERING CHECK' in captures[1]['messages'][-1]['content']
    assert workflow.report['disposition'] == 'checks_passed'
    assert sum(name == 'feature_extrude' for name, _ in bridge.calls) == 1
    assert sum(name == 'model_check' for name, _ in bridge.calls) == 2
    assert all('engineering_plan' in {t['function']['name'] for t in req['tools']} for req in captures)


async def test_agent_blocks_mutation_without_plan_and_does_not_mark_complete(tmp_path, monkeypatch):
    import backend.agent as module
    db, session, _, bridge, control, workflow, events, emit = setup(tmp_path)
    config = SettingsStore(tmp_path / 'settings.json')
    monkeypatch.setenv('FORMA_API_KEY', 'fixture')
    captures = []
    monkeypatch.setattr(module, 'AsyncOpenAI', lambda **kw: FakeClient([
        [chunk(calls=[tool('feature_extrude', {}, 'premature')])], [chunk('Done.')],
    ], captures))
    agent = CadAgent(db, config, bridge, KnowledgeBase(db, config))
    agent.control = control
    with pytest.raises(EngineeringReviewRequired):
        await agent.run(session['id'], 'feature_extrude fixture', emit)
    assert not any(name == 'feature_extrude' for name, _ in bridge.calls)
    result = next(e for e in events if e.get('call_id') == 'premature' and e['type'] == 'tool_result')
    assert result['outcome'] == 'not_executed'
    assert workflow.report['disposition'] == 'needs_review'


async def test_api_review_stop_persists_report_and_releases_lock(tmp_path, monkeypatch):
    monkeypatch.setenv('FORMA_API_KEY', 'fixture')
    db = Database(tmp_path / 'api.db')
    config = SettingsStore(tmp_path / 'settings.json')
    app = create_app(db, config, bridge=EngineeringBridge(), token='fixture')
    async def run(*args):
        engineering = app.state.agent.control.engineering
        assert engineering is not None
        await engineering.finish()
    monkeypatch.setattr(app.state.agent, 'run', run)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://testserver',
                                headers={'Authorization': 'Bearer fixture'}) as client:
        session = (await client.post('/api/sessions', json={})).json()
        response = await client.post('/api/chat', json={'session_id': session['id'], 'message': 'fixture', 'engineering_mode': True})
        assert response.status_code == 200
        task = db.runs(session['id'])[0]
        assert task['status'] == 'failed'
        assert (await client.get('/api/health')).json()['task_running'] is False
        report = (await client.get('/api/tasks/' + task['id'] + '/report')).json()['report']
        assert report['engineering']['disposition'] == 'needs_review'
        assert report['engineering']['production_released'] is False
        invalid = await client.post('/api/chat', json={'session_id': session['id'], 'message': 'fixture', 'engineering_mode': 'true'})
        assert invalid.status_code == 422
