"""Rule loading and evidence-integrity regressions, using synthetic CAD only."""
import copy

import pytest
from jsonschema import Draft202012Validator

from backend.engineering import EngineeringReviewRequired, TOOLS, evaluate
from backend.engineering_rules import PROCESSES, load_rules
from backend.execution import build_report
from test_engineering import PLAN, confirm, setup


@pytest.mark.parametrize('process', PROCESSES)
def test_rule_packs_are_versioned_detached_and_not_standards(process):
    pack = load_rules(process)
    assert pack['version'] == '1.1.0'
    assert pack['authority'] == 'internal_checklist_not_manufacturing_standard'
    assert bool(pack['requirements']) == (process != 'concept')
    assert all(rule['checker'] == 'manual' for rule in pack['requirements'])
    pack['requirements'].clear()
    assert bool(load_rules(process)['requirements']) == (process != 'concept')


def test_tool_contracts_are_valid_json_schemas():
    for tool in TOOLS.values():
        Draft202012Validator.check_schema(tool['function']['parameters'])


async def test_rules_are_available_before_plan_and_automatically_frozen(tmp_path):
    db, _, task, _, control, workflow, _, _ = setup(tmp_path)
    pack = await workflow.call_tool('engineering_rules', {'process': 'machining'})
    assert pack['process'] == 'machining' and workflow.plan is None
    async def accept(args):
        assert 'forma.machining v1.1.0' in args['question']
        assert '刀具可达性' in args['question']
        return {'cancelled': False, 'selected_option_ids': ['accept'], 'text': ''}
    control.ask_question = accept
    await workflow.call_tool('engineering_plan', {**PLAN, 'process': 'machining'})
    report = await workflow.call_tool('engineering_check', {})
    assert report['disposition'] == 'needs_review'
    assert report['rules_hash'] and report['rule_pack'] == pack
    for rule in pack['requirements']:
        assert next(i for i in report['items'] if i['id'] == rule['id'])['status'] == 'UNKNOWN'
    exported = build_report(db, task['id'])
    assert len(exported['engineering_history']) == 2
    assert exported['engineering_history'][-1]['id'] == report['id']


@pytest.mark.parametrize('changed', ['plan', 'rule_pack'])
async def test_in_memory_contract_changes_stop_checks_and_mutations(tmp_path, changed):
    _, _, _, _, control, workflow, _, _ = setup(tmp_path)
    await confirm(workflow, control)
    getattr(workflow, changed)['unexpected'] = True
    with pytest.raises(EngineeringReviewRequired, match='发生变化'):
        await workflow.check()
    with pytest.raises(EngineeringReviewRequired, match='发生变化'):
        await workflow.before_operation('feature_extrude', {})


@pytest.mark.parametrize('review,expected', [
    ({'issues': ['invalid']}, 'UNKNOWN'),
    ({'issues': [{'severity': 'error'}]}, 'FAIL'),
    ({'issues': [{'severity': 'warning', 'kind': 'needs_rebuild'}]}, 'UNKNOWN'),
    ({'incompleteReads': ['feature read missing']}, 'UNKNOWN'),
    ({'errorCount': True}, 'UNKNOWN'),
    ({'warningCount': 1}, 'UNKNOWN'),
    ({'warningCount': None}, 'UNKNOWN'),
])
def test_partial_or_contradictory_health_never_passes(review, expected):
    readings = {'model_info': {'doc': 'FixturePart', 'type': 'part'},
                'model_review': {'doc': 'FixturePart', 'errorCount': 0, 'complete': True, 'issues': [], **review}}
    items = evaluate(copy.deepcopy(PLAN), readings)
    assert next(i for i in items if i['id'] == '_health')['status'] == expected


async def test_new_check_invalidates_previous_pass_before_reading(tmp_path):
    _, _, _, bridge, control, workflow, _, _ = setup(tmp_path)
    await confirm(workflow, control)
    await workflow.check()
    original = bridge.call
    async def observe(name, args):
        assert workflow.report['disposition'] != 'checks_passed'
        return await original(name, args)
    bridge.call = observe
    assert (await workflow.check())['disposition'] == 'checks_passed'


@pytest.mark.parametrize('mode', ['changed', 'error', 'unchanged'])
async def test_final_status_must_match_checked_snapshot(tmp_path, mode):
    _, _, _, bridge, control, workflow, _, _ = setup(tmp_path)
    await confirm(workflow, control)
    await workflow.check()
    if mode == 'changed': bridge.stamp += 1
    result = await bridge.call('sw_status', {})
    if mode == 'error': result['is_error'] = True
    if mode == 'unchanged':
        await workflow.validate_final_state(result)
        assert workflow.report['disposition'] == 'checks_passed'
    else:
        with pytest.raises(EngineeringReviewRequired):
            await workflow.validate_final_state(result)
        assert workflow.report['disposition'] == 'stale'


@pytest.mark.parametrize('name,args', [
    ('vba_run', {'code': 'arbitrary fixture'}),
    ('doc_save', {'mode': 'as', 'path': 'renamed.sldprt'}),
    ('doc_open', {'path': 'different.sldprt'}),
])
async def test_unbounded_or_identity_changing_operations_are_held(tmp_path, name, args):
    _, _, _, bridge, control, workflow, _, _ = setup(tmp_path)
    await confirm(workflow, control)
    with pytest.raises(ValueError):
        await workflow.before_operation(name, args)
    assert all(call_name == 'sw_status' for call_name, _ in bridge.calls)


async def test_confirmed_material_is_checked_even_if_model_omits_material_rule(tmp_path):
    _, _, _, bridge, control, workflow, _, _ = setup(tmp_path)
    candidate = copy.deepcopy(PLAN)
    candidate['requirements'] = candidate['requirements'][:1]
    async def accept(_): return {'selected_option_ids': ['accept'], 'text': ''}
    control.ask_question = accept
    await workflow.propose(candidate)
    bridge.material = 'Copper'
    report = await workflow.check()
    assert report['disposition'] == 'repair_required'
    assert next(i for i in report['items'] if i['id'] == '_material')['status'] == 'FAIL'
