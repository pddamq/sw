"""Project scope regressions. All CAD responses are synthetic; no real model edits."""
import asyncio
import copy
import json

import pytest
from jsonschema import ValidationError

from backend.agent import CadAgent
from backend.config import SettingsStore
from backend.engineering import EngineeringReviewRequired, EngineeringStore, digest, document_alias
from backend.execution import ExecutionHalted
from backend.knowledge import KnowledgeBase
from test_agent import FakeClient, chunk
from test_engineering import PLAN, setup, tool


ROOT = {**copy.deepcopy(PLAN), 'target_document': 'CaseAssembly', 'document_type': 'assembly',
        'process': 'assembly', 'material': 'Per component', 'requirements': [
            {'id': 'interfaces', 'description': 'Preserve installation interfaces', 'checker': 'manual',
             'expected': 'Project interface drawing', 'source': 'User requirement'},
        ]}
CHILD = {**copy.deepcopy(PLAN), 'target_document': 'BasePlate', 'document_type': 'part'}


class ProjectBridge:
    detail = 'Synthetic project fixture'
    running = True

    def __init__(self):
        self.tools = [
            {'name': name, 'description': name, 'annotations': {'readOnlyHint': True, 'destructiveHint': False},
             'inputSchema': {'type': 'object', 'properties': {} if name == 'sw_status' else {'doc': {'type': 'string'}},
                             'additionalProperties': False}}
            for name in ('sw_status', 'model_info', 'model_review', 'model_check')]
        self.tools += [{'name': 'sketch_start', 'description': 'Start a fixture sketch',
            'annotations': {'readOnlyHint': False, 'destructiveHint': False},
            'inputSchema': {'type': 'object', 'properties': {'doc': {'type': 'string'}}, 'additionalProperties': False}}]
        self.docs = [
            {'title': 'CaseAssembly', 'handle': 'D1', 'type': 'assembly', 'configuration': 'Default',
             'updateStamp': 1, 'target': True, 'path': 'C:/fixtures/CaseAssembly.sldasm'},
            {'title': 'BasePlate', 'handle': 'D2', 'type': 'part', 'configuration': 'Default',
             'updateStamp': 1, 'target': False, 'path': 'C:/fixtures/BasePlate.sldprt'},
            {'title': 'PersonalPart', 'handle': 'D9', 'type': 'part', 'configuration': 'Default',
             'updateStamp': 1, 'target': False, 'path': 'C:/fixtures/PersonalPart.sldprt'},
        ]
        self.calls = []
        self.body_count = 1
        self.component_count = 1
        self.change_during_read = False
        self.fail_child = False
        self.sw_status = self.status()

    def status(self):
        return {'attached': True, 'solidWorksPid': 1, 'documents': copy.deepcopy(self.docs),
                'targetDocument': next((d['title'] for d in self.docs if d['target']), None)}

    async def call(self, name, arguments):
        self.calls.append((name, copy.deepcopy(arguments)))
        if name == 'sw_status':
            data = self.status()
        else:
            title = arguments.get('doc', self.status()['targetDocument'])
            doc = next(d for d in self.docs if document_alias(title) in {
                document_alias(d.get(key)) for key in ('title', 'handle', 'path')})
            title = doc['title']
            if self.fail_child and title == 'BasePlate':
                raise RuntimeError('fixture transport failure')
            if name == 'sketch_start':
                doc['updateStamp'] += 1
                data = {'doc': title}
            elif name == 'model_info':
                data = {'doc': title, 'type': doc['type'], 'material': 'Steel',
                        'bodies': [{'type': 'solid'} for _ in range(self.body_count)], 'components': self.component_count}
            elif name == 'model_review':
                data = {'doc': title, 'errorCount': 0, 'warningCount': 0, 'issues': [], 'complete': True}
            elif name == 'model_check':
                data = {'doc': title, 'valid': True, 'faultCount': 0, 'bodies': [{'valid': True, 'faults': []}]}
                if self.change_during_read:
                    self.docs[0]['updateStamp'] += 1
            else:
                raise AssertionError(name)
        return {'content': json.dumps(data), 'is_error': False}


async def project(tmp_path, root=None):
    values = list(setup(tmp_path))
    db, session, task, _, control, workflow, events, emit = values
    bridge = ProjectBridge()
    control.bridge = bridge
    values[3] = bridge
    questions = []
    async def accept(args):
        questions.append(args)
        return {'selected_option_ids': ['accept'], 'text': '', 'cancelled': False}
    control.ask_question = accept
    await workflow.propose(copy.deepcopy(root or ROOT))
    return values, questions


async def enroll(workflow, child=None):
    return await workflow.call_tool('engineering_scope_extend', {'documents': [copy.deepcopy(child or CHILD)]})


@pytest.mark.parametrize('address', ['BasePlate', 'D2', 'C:\\fixtures\\BasePlate.sldprt'])
async def test_original_block_is_resolved_by_explicit_child_confirmation(tmp_path, address):
    (db, _, task, bridge, control, workflow, _, _), questions = await project(tmp_path)
    root, root_hash, rules_hash = copy.deepcopy(workflow.plan), workflow.plan_hash, workflow.rules_hash
    with pytest.raises(ValueError, match='engineering_scope_extend'):
        await workflow.before_operation('sketch_start', {'doc': address})
    result = await enroll(workflow)
    assert result['confirmed'] and 'BasePlate' in questions[-1]['question']
    assert '材料：Steel' in questions[-1]['question']
    assert workflow.plan == root and workflow.plan_hash == root_hash and workflow.rules_hash == rules_hash
    assert workflow.scope_hash == digest(workflow.scope_documents)
    assert not any(name == 'sketch_start' for name, _ in bridge.calls)
    await workflow.before_operation('sketch_start', {'doc': address})
    await control.invoke('sketch_start', {'doc': address})
    assert bridge.docs[1]['updateStamp'] == 2
    assert EngineeringStore(db).latest(task['id'])['disposition'] == 'stale'
    with pytest.raises(ValueError, match='范围'):
        await workflow.before_operation('sketch_start', {'doc': 'D9'})


async def test_implicit_target_must_be_enrolled_and_existing_parts_are_reused(tmp_path):
    (_, _, _, bridge, _, workflow, _, _), _ = await project(tmp_path)
    await enroll(workflow)
    for d in bridge.docs: d['target'] = d['title'] == 'BasePlate'
    await workflow.before_operation('sketch_start', {})
    with pytest.raises(ValueError, match='已经打开'):
        await workflow.before_operation('doc_new', {'name': 'BasePlate', 'type': 'part'})
    for d in bridge.docs: d['target'] = d['title'] == 'PersonalPart'
    with pytest.raises(ValueError, match='范围'):
        await workflow.before_operation('sketch_start', {})


async def test_new_child_title_and_type_are_enforced_before_creation(tmp_path):
    (_, _, _, bridge, _, workflow, _, _), _ = await project(tmp_path)
    with pytest.raises(ValueError, match='engineering_scope_extend'):
        await workflow.before_operation('doc_new', {'name': 'NewCover', 'type': 'part'})
    await enroll(workflow, {**CHILD, 'target_document': 'NewCover'})
    await workflow.before_operation('doc_new', {'name': 'NewCover', 'type': 'part'})
    with pytest.raises(ValueError, match='类型'):
        await workflow.before_operation('doc_new', {'name': 'NewCover', 'type': 'assembly'})
    bridge.docs[1]['type'] = 'assembly'
    await enroll(workflow)
    with pytest.raises(EngineeringReviewRequired, match='类型'):
        await workflow.before_operation('sketch_start', {'doc': 'D2'})


@pytest.mark.parametrize('answer', [
    {'cancelled': True, 'selected_option_ids': [], 'text': ''},
    {'selected_option_ids': ['revise'], 'text': 'change dimensions'},
    {'selected_option_ids': ['accept'], 'text': 'but use another material'},
])
async def test_scope_decline_is_atomic_and_cannot_relax_existing_requirements(tmp_path, answer):
    (_, _, _, _, control, workflow, _, _), _ = await project(tmp_path)
    old_hash = workflow.plan_hash, workflow.scope_hash
    count = 0
    async def respond(_):
        nonlocal count
        count += 1
        return {'selected_option_ids': ['accept'], 'text': ''} if count == 1 else answer
    control.ask_question = respond
    result = await workflow.extend_scope({'documents': [CHILD, {**CHILD, 'target_document': 'Cover'}]})
    assert not result['confirmed'] and not workflow.scope_documents
    assert (workflow.plan_hash, workflow.scope_hash) == old_hash
    with pytest.raises(ValueError):
        await workflow.before_operation('sketch_start', {'doc': 'D2'})


async def test_cancellation_between_cards_commits_no_partial_scope(tmp_path):
    (_, _, _, _, control, workflow, _, _), _ = await project(tmp_path)
    count = 0
    async def respond(_):
        nonlocal count
        count += 1
        if count == 2: raise asyncio.CancelledError()
        return {'selected_option_ids': ['accept'], 'text': ''}
    control.ask_question = respond
    with pytest.raises(asyncio.CancelledError):
        await workflow.extend_scope({'documents': [CHILD, {**CHILD, 'target_document': 'Cover'}]})
    assert workflow.scope_documents == []


@pytest.mark.parametrize('title', ['CaseAssembly', 'caseassembly', 'BasePlate', 'baseplate'])
async def test_append_never_replaces_existing_root_or_child(tmp_path, title):
    (_, _, _, _, _, workflow, _, _), _ = await project(tmp_path)
    await enroll(workflow)
    old_hash = workflow.plan_hash, workflow.scope_hash
    with pytest.raises(ValueError, match='重复'):
        await enroll(workflow, {**CHILD, 'target_document': title, 'material': 'Copper'})
    assert (workflow.plan_hash, workflow.scope_hash) == old_hash


async def test_child_contracts_remain_frozen_after_steering_and_main_reconfirmation(tmp_path):
    (_, _, _, _, _, workflow, _, _), _ = await project(tmp_path)
    await enroll(workflow)
    old_scope = copy.deepcopy(workflow.scope_documents)
    await workflow.invalidate('user steering', reconfirm=True)
    with pytest.raises(ValueError, match='engineering_plan_required'):
        await enroll(workflow, {**CHILD, 'target_document': 'Cover'})
    await workflow.propose(copy.deepcopy(ROOT))
    assert workflow.scope_documents == old_scope
    workflow.scope_documents[0]['plan']['material'] = 'Copper'
    with pytest.raises(EngineeringReviewRequired, match='发生变化'):
        await workflow.check()


async def test_every_document_is_read_and_child_failures_are_namespaced(tmp_path):
    (_, _, _, bridge, _, workflow, _, _), _ = await project(tmp_path)
    await enroll(workflow)
    bridge.body_count = 2
    report = await workflow.check()
    assert report['disposition'] == 'repair_required'
    assert {d['document'] for d in report['documents']} == {'CaseAssembly', 'BasePlate'}
    child = next(d for d in report['documents'] if d['document'] == 'BasePlate')
    item = next(i for i in child['items'] if i['requirement_id'] == 'bodies')
    assert item['status'] == 'FAIL' and item['id'].startswith('doc_')
    assert item['document'] == 'BasePlate'
    assert ('model_check', {'doc': 'BasePlate'}) in bridge.calls
    assert ('model_check', {'doc': 'CaseAssembly'}) not in bridge.calls
    assert all(args.get('doc') != 'PersonalPart' for name, args in bridge.calls if name != 'sw_status')
    feedback = await workflow.finish()
    assert 'BasePlate' in feedback and workflow.repair_attempts == 1
    bridge.body_count = 1
    with pytest.raises(EngineeringReviewRequired, match='未知'):
        await workflow.finish()
    assert workflow.report['production_released'] is False


async def test_closed_child_never_inherits_assembly_pass(tmp_path):
    (_, _, _, bridge, _, workflow, _, _), _ = await project(tmp_path)
    await enroll(workflow)
    bridge.docs = [d for d in bridge.docs if d['title'] != 'BasePlate']
    report = await workflow.check()
    child = next(d for d in report['documents'] if d['document'] == 'BasePlate')
    assert child['disposition'] == 'needs_review'
    assert all(i['status'] == 'UNKNOWN' for i in child['items'])
    assert not any(args.get('doc') == 'BasePlate' for _, args in bridge.calls)


async def test_child_read_failure_retains_unknown_checkpoint_and_stops(tmp_path):
    (db, _, task, bridge, _, workflow, _, _), _ = await project(tmp_path)
    await enroll(workflow)
    bridge.fail_child = True
    with pytest.raises(ExecutionHalted):
        await workflow.check()
    assert db.checkpoints(task['id'])[-1]['status'] == 'unknown'
    assert not any(name == 'model_check' for name, _ in bridge.calls)


async def test_change_to_parent_during_child_reads_invalidates_entire_snapshot(tmp_path):
    (_, _, _, bridge, _, workflow, _, _), _ = await project(tmp_path)
    await enroll(workflow)
    bridge.change_during_read = True
    report = await workflow.check()
    assert all(i['status'] == 'UNKNOWN' for i in report['items'])
    assert all(d['disposition'] == 'needs_review' for d in report['documents'])


async def test_empty_assembly_is_a_known_defect(tmp_path):
    (_, _, _, bridge, _, workflow, _, _), _ = await project(tmp_path)
    bridge.component_count = 0
    report = await workflow.check()
    assert next(i for i in report['items'] if i['id'] == '_assembly_content')['status'] == 'FAIL'
    assert next(i for i in report['items'] if i['id'] == '_material')['status'] == 'NOT_APPLICABLE'
    assert report['disposition'] == 'repair_required'


async def test_scope_extension_invalidates_previous_pass(tmp_path):
    root = {**copy.deepcopy(CHILD), 'target_document': 'PersonalPart'}
    (_, _, _, _, _, workflow, _, _), _ = await project(tmp_path, root)
    await workflow.check()
    assert workflow.report['disposition'] == 'checks_passed'
    await enroll(workflow)
    assert workflow.report['disposition'] == 'stale'
    assert workflow.checks == 1
    assert workflow.repair_attempts == 0


async def test_new_local_tool_is_advertised_and_resolves_real_agent_scope_error(tmp_path, monkeypatch):
    import backend.agent as module
    (db, session, _, bridge, control, workflow, events, emit), _ = await project(tmp_path)
    monkeypatch.setenv('FORMA_API_KEY', 'fixture')
    captures = []
    monkeypatch.setattr(module, 'AsyncOpenAI', lambda **kw: FakeClient([
        [chunk(calls=[tool('sketch_start', {'doc': 'D2'}, 'blocked')])],
        [chunk(calls=[tool('engineering_scope_extend', {'documents': [CHILD]}, 'extend')])],
        [chunk(calls=[tool('sketch_start', {'doc': 'D2'}, 'edit')])],
        [chunk('Await project review.')],
    ], captures))
    settings = SettingsStore(tmp_path / 'settings.json')
    agent = CadAgent(db, settings, bridge, KnowledgeBase(db, settings))
    agent.control = control
    with pytest.raises(EngineeringReviewRequired):
        await agent.run(session['id'], 'sketch_start BasePlate', emit)
    assert sum(name == 'sketch_start' for name, _ in bridge.calls) == 1
    assert len(captures) == 4
    assert all('engineering_scope_extend' in {t['function']['name'] for t in request['tools']} for request in captures)
    assert 'BasePlate' in captures[2]['messages'][0]['content']
    blocked = next(e for e in events if e.get('call_id') == 'blocked' and e['type'] == 'tool_result')
    assert blocked['outcome'] == 'not_executed'
    assert workflow.plan == ROOT


@pytest.mark.parametrize('kind', ['duplicate_title', 'ambiguous_alias', 'missing_default'])
async def test_ambiguous_live_identity_never_allows_child_edit(tmp_path, kind):
    (_, _, _, bridge, _, workflow, _, _), _ = await project(tmp_path)
    await enroll(workflow)
    if kind == 'duplicate_title':
        bridge.docs.append({**bridge.docs[1], 'handle': 'D3', 'title': 'baseplate'})
    elif kind == 'ambiguous_alias':
        bridge.docs[2]['handle'] = 'D2'
    else:
        for d in bridge.docs: d['target'] = False
    with pytest.raises((ValueError, EngineeringReviewRequired)):
        await workflow.before_operation('sketch_start', {} if kind == 'missing_default' else {'doc': 'D2'})


@pytest.mark.parametrize('invalid', ['missing_type', 'no_requirements', 'duplicate_batch', 'overlong'])
async def test_invalid_scope_is_rejected_before_any_confirmation(tmp_path, invalid):
    (_, _, _, _, _, workflow, _, _), questions = await project(tmp_path)
    docs = [copy.deepcopy(CHILD)]
    if invalid == 'missing_type': docs[0].pop('document_type')
    if invalid == 'no_requirements': docs[0]['requirements'] = []
    if invalid == 'duplicate_batch': docs.append(copy.deepcopy(CHILD))
    if invalid == 'overlong':
        docs[0]['requirements'] = [{**CHILD['requirements'][0], 'id': 'r' + str(i),
            'description': 'x' * 100, 'expected': 'x' * 100, 'source': 'x' * 100, 'checker': 'manual'} for i in range(8)]
    with pytest.raises((ValueError, ValidationError)):
        await workflow.extend_scope({'documents': docs})
    assert len(questions) == 1
    assert not workflow.scope_documents


async def test_scope_limits_do_not_reset_repair_or_check_budgets(tmp_path):
    (_, _, _, _, _, workflow, _, _), _ = await project(tmp_path)
    workflow.repair_attempts, workflow.checks = 2, 4
    workflow.MAX_DOCUMENTS = 2
    await enroll(workflow)
    assert (workflow.repair_attempts, workflow.checks) == (2, 4)
    with pytest.raises(ValueError, match='16'):
        await enroll(workflow, {**CHILD, 'target_document': 'Cover'})
    workflow.MAX_DOCUMENTS = 16
    workflow.scope_proposals = workflow.MAX_SCOPE_PROPOSALS
    with pytest.raises(EngineeringReviewRequired, match='预算'):
        await enroll(workflow, {**CHILD, 'target_document': 'Cover'})


async def test_confirmed_extension_changes_progress_baseline_but_not_repair_budget(tmp_path):
    (_, _, _, bridge, _, workflow, _, _), _ = await project(tmp_path)
    bridge.component_count = 0
    await workflow.finish()
    assert workflow.repair_attempts == 1
    await enroll(workflow)
    bridge.body_count = 2
    feedback = await workflow.finish()
    assert 'BasePlate' in feedback and workflow.repair_attempts == 2
    with pytest.raises(EngineeringReviewRequired, match='缺陷未减少'):
        await workflow.finish()


async def test_scope_hash_and_history_survive_store_reload(tmp_path):
    (db, _, task, _, _, workflow, _, _), _ = await project(tmp_path)
    await enroll(workflow)
    await workflow.check()
    records = EngineeringStore(db).history(task['id'])
    assert len(records) == 3
    assert records[0]['scope_documents'] == []
    assert records[1]['scope_documents'][0]['plan'] == CHILD
    assert records[2]['scope_hash'] == workflow.scope_hash
    assert records[2]['documents'][1]['document'] == 'BasePlate'
