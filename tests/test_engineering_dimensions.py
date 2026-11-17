"""Synthetic regression cases for named dimensions and annotation envelopes."""
import copy
import json

import pytest
from jsonschema import ValidationError

from backend.engineering_dimensions import evaluate_dimension
from backend.engineering import EngineeringReviewRequired
from test_engineering import PLAN, setup


RULE = {'id': 'width', 'description': 'Named width parameter', 'checker': 'dimension_range',
        'expected': '19.9 to 20.1 mm', 'source': 'Confirmed fixture requirement',
        'measurement': {'name': 'D1@Sketch1', 'unit': 'mm', 'minimum': 19.9, 'maximum': 20.1}}


def reading(value=20, **extra):
    return {'doc': 'FixturePart', 'complete': True, 'total': 1,
            'dimensions': [{'name': 'D1@Sketch1', 'value': value, 'unit': 'mm', **extra}]}


def check(data, rule=None):
    return evaluate_dimension(rule or copy.deepcopy(RULE), data, 'FixturePart')


@pytest.mark.parametrize('value,status', [(19.9, 'PASS'), (20, 'PASS'), (20.1, 'PASS'), (19.899, 'FAIL'), (20.101, 'FAIL')])
def test_inclusive_parameter_bounds(value, status):
    result = check(reading(value))
    assert result['status'] == status
    assert result['actual']['value'] == value
    assert '最终实体' in result['reason']


@pytest.mark.parametrize('value', [None, '20', True, float('inf'), float('-inf'), float('nan')])
def test_invalid_numeric_evidence_is_unknown(value):
    assert check(reading(value))['status'] == 'UNKNOWN'


@pytest.mark.parametrize('patch', [
    {'doc': 'Other'}, {'complete': False}, {'complete': None}, {'truncated': True},
    {'total': 2}, {'total': True}, {'dimensions': []}, {'dimensions': ['bad']},
    {'incompleteReads': ['enumeration limit']}, {'warnings': ['missing dimension']},
])
def test_partial_lists_never_pass(patch):
    assert check({**reading(), **patch})['status'] == 'UNKNOWN'


def test_missing_duplicate_and_unit_mismatch_are_unknown():
    data = reading()
    data['dimensions'][0]['name'] = 'D2@Sketch1'
    assert check(data)['status'] == 'UNKNOWN'
    data = reading()
    data['dimensions'] *= 2
    data['total'] = 2
    assert check(data)['status'] == 'UNKNOWN'
    assert check(reading(unit='deg'))['status'] == 'UNKNOWN'
    data = reading()
    data['dimensions'].append({**data['dimensions'][0], 'name': 'd1@sketch1'})
    data['total'] = 2
    assert check(data)['status'] == 'UNKNOWN'


@pytest.mark.parametrize('unit,value,minimum,maximum,status', [
    ('deg', 90, 89.5, 90.5, 'PASS'), ('deg', 91, 89.5, 90.5, 'FAIL'),
    ('count', 4, 4, 4, 'PASS'), ('count', 4.5, 4, 5, 'UNKNOWN'),
])
def test_explicit_angular_and_count_units(unit, value, minimum, maximum, status):
    rule = copy.deepcopy(RULE)
    rule['measurement'].update(unit=unit, minimum=minimum, maximum=maximum)
    assert check(reading(value, unit=unit), rule)['status'] == status


def tolerance_rule():
    return {**copy.deepcopy(RULE), 'checker': 'dimension_tolerance_range'}


@pytest.mark.parametrize('kind', ['symmetric', 'bilateral', 'limit'])
def test_tolerance_envelope_uses_nominal_plus_deviations(kind):
    data = reading(tolerance={'type': kind, 'lower': -0.1, 'upper': 0.1})
    result = check(data, tolerance_rule())
    assert result['status'] == 'PASS'
    assert result['actual']['minimum'] == 19.9
    assert result['actual']['maximum'] == 20.1
    data['dimensions'][0]['tolerance']['upper'] = 0.11
    if kind == 'symmetric': data['dimensions'][0]['tolerance']['lower'] = -0.11
    assert check(data, tolerance_rule())['status'] == 'FAIL'


@pytest.mark.parametrize('tol', [None, {}, {'type': 'none'}, {'type': 'fit', 'hole': 'H7'},
    {'type': 'bilateral', 'upper': 0.1}, {'type': 'bilateral', 'upper': True, 'lower': -0.1},
    {'type': 'bilateral', 'upper': -0.2, 'lower': 0.1},
    {'type': 'symmetric', 'upper': 0.1, 'lower': -0.2}])
def test_unknown_tolerances_are_not_invented(tol):
    assert check(reading(tolerance=tol), tolerance_rule())['status'] == 'UNKNOWN'


async def dimension_fixture(tmp_path, rule=None):
    values = setup(tmp_path)
    _, _, _, bridge, control, workflow, _, _ = values
    bridge.dimension_data = reading()
    bridge.tools = [*bridge.tools, {'name': 'dimension_list', 'annotations': {'readOnlyHint': True},
        'inputSchema': {'type': 'object', 'properties': {'doc': {'type': 'string'},
            'limit': {'type': 'integer'}, 'offset': {'type': 'integer'}}, 'additionalProperties': False}}]
    original = bridge.call
    async def call(name, args):
        if name == 'dimension_list':
            bridge.calls.append((name, args))
            return {'is_error': False, 'content': json.dumps(bridge.dimension_data)}
        return await original(name, args)
    bridge.call = call
    questions = []
    async def accept(args):
        questions.append(args['question'])
        return {'selected_option_ids': ['accept'], 'text': ''}
    control.ask_question = accept
    candidate = {**copy.deepcopy(PLAN), 'requirements': [copy.deepcopy(rule or RULE)]}
    return values, candidate, questions


async def test_live_tool_reading_is_used_and_bounds_shown_before_confirmation(tmp_path):
    values, plan, questions = await dimension_fixture(tmp_path)
    _, _, _, bridge, _, workflow, _, _ = values
    await workflow.propose(plan)
    assert 'D1@Sketch1 [19.9, 20.1] mm' in questions[0]
    report = await workflow.check()
    assert report['disposition'] == 'checks_passed'
    assert ('dimension_list', {'doc': 'FixturePart', 'limit': 1000, 'offset': 0}) in bridge.calls
    plan['requirements'][0]['measurement']['maximum'] = 200
    bridge.dimension_data = reading(21)
    result = await workflow.check()
    assert result['disposition'] == 'repair_required'
    assert next(i for i in result['items'] if i['id'] == 'width')['expected']['maximum'] == 20.1


async def test_missing_dimension_tool_stops_without_repair(tmp_path):
    values, plan, _ = await dimension_fixture(tmp_path)
    _, _, _, bridge, _, workflow, _, _ = values
    await workflow.propose(plan)
    bridge.tools = [t for t in bridge.tools if t['name'] != 'dimension_list']
    with pytest.raises(EngineeringReviewRequired):
        await workflow.finish()
    assert workflow.repair_attempts == 0
    assert workflow.report['disposition'] == 'needs_review'


@pytest.mark.parametrize('measurement', [None, {},
    {'name': 'D1@Sketch1', 'unit': 'cm', 'minimum': 1, 'maximum': 2},
    {'name': 'D1@Sketch1', 'unit': 'mm', 'minimum': 2, 'maximum': 1},
    {'name': 'D1@Sketch1', 'unit': 'mm', 'minimum': float('nan'), 'maximum': 2},
    {'name': 'D1@Sketch1', 'unit': 'count', 'minimum': 1.5, 'maximum': 2},
])
async def test_bad_measurement_rejected_before_confirmation(tmp_path, measurement):
    rule = {**copy.deepcopy(RULE), 'measurement': measurement}
    values, plan, questions = await dimension_fixture(tmp_path, rule)
    workflow = values[5]
    with pytest.raises((ValueError, ValidationError)):
        await workflow.propose(plan)
    assert not questions and workflow.plan is None


async def test_measurements_cannot_be_silently_attached_to_manual_rules(tmp_path):
    values, plan, questions = await dimension_fixture(tmp_path, {**copy.deepcopy(RULE), 'checker': 'manual'})
    with pytest.raises(ValueError):
        await values[5].propose(plan)
    assert not questions
