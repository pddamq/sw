"""Boundary regressions for the seven engineering/citation review findings. Synthetic CAD only."""
import copy
import json
import ntpath

import pytest

from backend.agent import CadAgent
from backend.config import SettingsStore
from backend.engineering import EngineeringReviewRequired, EngineeringStore
from backend.knowledge import KnowledgeBase
from backend.tool_snapshot import READ_ONLY_TOOL_NAMES
from test_agent import FakeClient, chunk
from test_engineering import confirm, setup
from test_engineering_project import CHILD, ROOT, ProjectBridge, enroll, project


@pytest.mark.parametrize('change', [
    {'path': 'C:/another/BasePlate.sldprt'}, {'handle': 'D77'}, {'title': 'baseplate'},
])
async def test_same_name_replacements_never_inherit_binding(tmp_path, change):
    values, _ = await project(tmp_path)
    _, _, _, bridge, _, workflow, _, _ = values
    await enroll(workflow)
    bridge.docs[1].update(change)
    with pytest.raises((ValueError, EngineeringReviewRequired)):
        await workflow.before_operation('sketch_start', {'doc': bridge.docs[1]['handle']})
    report = await workflow.check()
    child = report['documents'][1]
    assert all(item['status'] == 'UNKNOWN' for item in child['items'])
    assert not any(name != 'sw_status' and args.get('doc') in ('BasePlate', 'baseplate') for name, args in bridge.calls)


async def test_pid_or_executor_owner_changes_stop_mutations(tmp_path):
    values, _ = await project(tmp_path)
    bridge, workflow = values[3], values[5]
    await enroll(workflow)
    original = bridge.status
    bridge.status = lambda: {**original(), 'solidWorksPid': 99}
    with pytest.raises(EngineeringReviewRequired):
        await workflow.before_operation('sketch_start', {'doc': 'D2'})
    bridge.status = original
    bridge.task = object()
    with pytest.raises(EngineeringReviewRequired, match='连接已更换'):
        await workflow.before_operation('sketch_start', {'doc': 'D2'})


async def test_scope_wait_does_not_accept_replacement_or_partial_bindings(tmp_path):
    values, _ = await project(tmp_path)
    bridge, control, workflow = values[3:6]
    async def accept(_):
        bridge.docs[1]['path'] = 'C:/different/BasePlate.sldprt'
        return {'selected_option_ids': ['accept'], 'text': ''}
    control.ask_question = accept
    with pytest.raises(EngineeringReviewRequired):
        await enroll(workflow)
    assert workflow.scope_documents == []
    assert 'BasePlate' not in workflow.bindings.entries


async def test_uncreated_contract_does_not_adopt_a_document_that_appears_later(tmp_path):
    values, _ = await project(tmp_path)
    bridge, workflow = values[3], values[5]
    await enroll(workflow, {**CHILD, 'target_document': 'NewCover'})
    bridge.docs.append({**bridge.docs[1], 'title': 'NewCover', 'handle': 'D8'})
    with pytest.raises(EngineeringReviewRequired):
        await workflow.before_operation('sketch_start', {'doc': 'D8'})


@pytest.mark.parametrize('name', ['sketch_info', 'body_list', 'face_list', 'edge_list', 'vertex_list',
    'material_list', 'prop_list', 'equation_list', 'config_list', 'asm_component_list', 'asm_mate_list', 'drw_info'])
async def test_pinned_queries_do_not_invalidate_or_spend_repair_budget(tmp_path, name):
    values, _ = await project(tmp_path, {**CHILD, 'target_document': 'BasePlate'})
    workflow = values[5]
    await workflow.check()
    report_id, epoch = workflow.report['id'], workflow.mutation_epoch
    workflow.repair_active = True
    for _ in range(35):
        assert await workflow.before_operation(name, {'doc': 'D9'}) == {'doc': 'D9'}
    assert name in READ_ONLY_TOOL_NAMES
    assert workflow.report['id'] == report_id and workflow.mutation_epoch == epoch
    assert workflow.repair_operations == 0


@pytest.mark.parametrize('name,args', [
    ('asm_structure', {'kind': 'form', 'components': ['base-1'], 'saveTo': 'hidden.sldasm'}),
    ('asm_structure', {'kind': ' FORM ', 'components': ['base-1']}),
    ('asm_pattern', {'kind': 'mirror', 'oppositeHand': True}),
])
async def test_implicit_native_document_creation_is_held(tmp_path, name, args):
    values, _ = await project(tmp_path)
    bridge, workflow = values[3], values[5]
    with pytest.raises(ValueError, match='额外 CAD 文档'):
        await workflow.before_operation(name, {'doc': 'D1', **args})
    assert all(n == 'sw_status' for n, _ in bridge.calls)


async def test_instance_patterns_remain_available(tmp_path):
    values, _ = await project(tmp_path)
    assert await values[5].before_operation('asm_pattern', {'doc': 'D1', 'kind': 'mirror', 'oppositeHand': False})


class LifecycleBridge(ProjectBridge):
    def __init__(self):
        super().__init__()
        self.tools += [{'name': name, 'annotations': {'readOnlyHint': False, 'destructiveHint': False},
            'inputSchema': {'type': 'object', 'properties': {'doc': {'type': 'string'}}}}
            for name in ('doc_new', 'doc_save', 'drw_sheet_setup')]
        self.tools += [{'name': 'drw_info', 'annotations': {'readOnlyHint': True},
            'inputSchema': {'type': 'object', 'properties': {'doc': {'type': 'string'}}}}]
        self.bad_save = False

    async def call(self, name, args):
        if name == 'doc_new':
            self.calls.append((name, copy.deepcopy(args)))
            for d in self.docs: d['target'] = False
            doc = {'title': args['name'], 'handle': 'D20', 'path': None, 'type': args.get('type', 'part'),
                   'access': 'owned', 'configuration': 'Default', 'updateStamp': 1, 'target': True}
            self.docs.append(doc)
            return {'is_error': False, 'content': json.dumps({'doc': doc['title'], 'document': doc})}
        if name in {'doc_save', 'drw_info', 'drw_sheet_setup'}:
            self.calls.append((name, copy.deepcopy(args)))
            doc = next(d for d in self.docs if args.get('doc') in (d['title'], d['handle'], d['path']))
            data = {'doc': doc['title']}
            if name == 'doc_save':
                path = args['path'].replace('\\', '/')
                extension = {'part': '.sldprt', 'assembly': '.sldasm', 'drawing': '.slddrw'}[doc['type']]
                if not path.lower().endswith(extension): path += extension
                if not ntpath.isabs(path): path = 'C:/output/' + path
                if args['mode'] == 'as':
                    doc.update(path=path, title=ntpath.basename(path), updateStamp=doc['updateStamp'] + 1)
                data.update(doc=doc['title'], document=copy.deepcopy(doc), savedTo=path)
                if self.bad_save: data['savedTo'] = 'C:/wrong/Other.sldprt'
            elif name == 'drw_info':
                data['sheets'] = [{'name': 'Sheet1', 'views': []}]
            return {'is_error': False, 'content': json.dumps(data)}
        return await super().call(name, args)


async def lifecycle(tmp_path, kind='part'):
    values, _ = await project(tmp_path)
    bridge = LifecycleBridge()
    values[3] = bridge
    values[4].bridge = bridge
    workflow = values[5]
    candidate = {**copy.deepcopy(CHILD), 'target_document': 'NewItem', 'document_type': kind}
    if kind == 'drawing': candidate['requirements'] = copy.deepcopy(ROOT['requirements'])
    await enroll(workflow, candidate)
    approvals = []
    async def approve(name, arguments, reason, purpose=None):
        approvals.append((name, copy.deepcopy(arguments)))
    values[4].approve = approve
    return values, approvals


async def execute(values, name, args):
    args = await values[5].before_operation(name, args)
    return await values[4].invoke(name, args, purpose='Fixture operation')


@pytest.mark.parametrize('path', ['C:/output/NewItem.sldprt', 'NewItem', 'nested/NewItem.sldprt'])
async def test_first_save_binds_same_document_without_changing_contract(tmp_path, path):
    values, approvals = await lifecycle(tmp_path)
    workflow = values[5]
    hashes = workflow.plan_hash, workflow.scope_hash
    await execute(values, 'doc_new', {'name': 'NewItem', 'type': 'part'})
    await execute(values, 'doc_save', {'doc': 'D20', 'mode': 'as', 'path': path})
    binding = workflow.bindings.entries['NewItem']
    assert binding['handle'] == 'D20' and binding['path'] and binding['title'] == 'NewItem.sldprt'
    prepared = await workflow.before_operation('sketch_start', {'doc': 'NewItem'})
    assert prepared['doc'] == 'D20'
    report = await workflow.check()
    assert all(i['status'] == 'PASS' for i in report['documents'][1]['items'])
    assert report['document_bindings']['NewItem'] == binding
    assert (workflow.plan_hash, workflow.scope_hash) == hashes
    assert approvals and approvals[0][0] == 'doc_save'
    with pytest.raises(ValueError, match='已有文件'):
        await workflow.before_operation('doc_save', {'doc': 'D20', 'mode': 'as', 'path': path})


async def test_user_reconfirmation_after_first_save_keeps_live_binding(tmp_path):
    values, _ = await lifecycle(tmp_path)
    await execute(values, 'doc_new', {'name': 'NewItem', 'type': 'part'})
    await execute(values, 'doc_save', {'doc': 'D20', 'mode': 'as', 'path': 'NewItem'})
    workflow = values[5]
    child_binding = copy.deepcopy(workflow.bindings.entries['NewItem'])
    await workflow.invalidate('steering', reconfirm=True)
    await workflow.propose(copy.deepcopy(ROOT))
    assert workflow.bindings.entries['NewItem'] == child_binding


@pytest.mark.parametrize('mode', ['renamed', 'foreign', 'wrong_result', 'changed_during_approval'])
async def test_first_save_cannot_rebind_other_files(tmp_path, mode):
    values, _ = await lifecycle(tmp_path)
    await execute(values, 'doc_new', {'name': 'NewItem', 'type': 'part'})
    bridge, control, workflow = values[3:6]
    if mode == 'foreign': bridge.docs[-1]['access'] = 'foreign'
    if mode == 'wrong_result': bridge.bad_save = True
    if mode == 'changed_during_approval':
        async def approve(*args, **kwargs): bridge.docs[-1]['handle'] = 'D21'
        control.approve = approve
    args = {'doc': 'D20', 'mode': 'as', 'path': 'Renamed' if mode == 'renamed' else 'NewItem'}
    with pytest.raises((ValueError, EngineeringReviewRequired)):
        await execute(values, 'doc_save', args)
    assert workflow.bindings.entries['NewItem']['path'] is None
    assert workflow.report['disposition'] != 'checks_passed'
    if mode != 'wrong_result': assert not any(name == 'doc_save' for name, _ in bridge.calls)


async def test_implicit_target_is_pinned_before_approval(tmp_path):
    values, _ = await lifecycle(tmp_path)
    await execute(values, 'doc_new', {'name': 'NewItem', 'type': 'part'})
    bridge, control = values[3:5]
    async def approve(*args, **kwargs):
        for doc in bridge.docs: doc['target'] = doc['handle'] == 'D9'
    control.approve = approve
    await execute(values, 'doc_save', {'mode': 'as', 'path': 'NewItem'})
    assert next(args for name, args in bridge.calls if name == 'doc_save')['doc'] == 'D20'


async def test_drawing_can_be_confirmed_created_saved_and_checked_conservatively(tmp_path):
    values, _ = await lifecycle(tmp_path, 'drawing')
    await execute(values, 'doc_new', {'name': 'NewItem', 'type': 'drawing'})
    values[3].docs[-1]['configuration'] = None
    await execute(values, 'drw_sheet_setup', {'doc': 'D20'})
    await execute(values, 'doc_save', {'doc': 'D20', 'mode': 'as', 'path': 'NewItem.slddrw'})
    report = await values[5].check()
    child = report['documents'][1]
    assert child['disposition'] == 'needs_review'
    statuses = {item['requirement_id']: item['status'] for item in child['items']}
    assert statuses['_target'] == 'PASS' and statuses['_document_type'] == 'PASS'
    assert statuses['_geometry'] == statuses['_material'] == 'NOT_APPLICABLE'
    assert statuses['_drawing_delivery'] == 'UNKNOWN'
    assert ('drw_info', {'doc': 'NewItem.slddrw'}) in values[3].calls
    assert not any(name in {'model_review', 'model_check', 'dimension_list'} and args.get('doc') == 'NewItem.slddrw'
                   for name, args in values[3].calls)
    assert report['production_released'] is False


@pytest.mark.parametrize('patch', [{'warnings': ['partial status']}, {'truncated': True}, {'documents': None}])
async def test_final_partial_status_never_preserves_pass(tmp_path, patch):
    _, _, _, bridge, control, workflow, _, _ = setup(tmp_path)
    await confirm(workflow, control)
    await workflow.check()
    result = await bridge.call('sw_status', {})
    result['content'] = json.dumps({**json.loads(result['content']), **patch})
    with pytest.raises(EngineeringReviewRequired):
        await workflow.validate_final_state(result)
    assert workflow.report['disposition'] == 'stale'


@pytest.mark.parametrize('mode', ['disconnect', 'remove_tool', 'replace_executor', 'final_exception'])
async def test_agent_never_completes_without_final_live_status(tmp_path, monkeypatch, mode):
    db, session, task, bridge, control, workflow, _, emit = setup(tmp_path)
    await confirm(workflow, control)
    original = workflow.finish
    async def changed_after_check():
        outcome = await original()
        if mode == 'disconnect': bridge.running = False
        elif mode == 'remove_tool': bridge.tools = [t for t in bridge.tools if t['name'] != 'sw_status']
        elif mode == 'replace_executor': bridge.task = object()
        elif mode == 'final_exception':
            original_invoke = control.invoke
            async def fail_final(name, arguments, purpose=None):
                if name == 'sw_status': raise RuntimeError('Synthetic final status failure')
                return await original_invoke(name, arguments, purpose)
            control.invoke = fail_final
        return outcome
    workflow.finish = changed_after_check
    monkeypatch.setenv('FORMA_API_KEY', 'fixture')
    monkeypatch.setattr('backend.agent.AsyncOpenAI', lambda **kw: FakeClient([[chunk('Fixture complete.')]], []))
    settings = SettingsStore(tmp_path / 'settings.json')
    agent = CadAgent(db, settings, bridge, KnowledgeBase(db, settings))
    agent.control = control
    with pytest.raises(EngineeringReviewRequired, match='收尾'):
        await agent.run(session['id'], 'Inspect fixture', emit)
    assert EngineeringStore(db).latest(task['id'])['disposition'] == 'stale'
