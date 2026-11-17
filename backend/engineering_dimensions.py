"""Deterministic checks of named CAD dimensions, never inferred geometry.

Ranges are user-confirmed closed intervals in the executor's explicit unit.
Tolerance-envelope checks concern CAD annotations, not measured manufactured parts.
"""
from __future__ import annotations

import math
from decimal import Decimal

CHECKERS = ('dimension_range', 'dimension_tolerance_range')
MEASUREMENT_SCHEMA = {
    'type': 'object', 'additionalProperties': False,
    'properties': {
        'name': {'type': 'string', 'minLength': 3, 'maxLength': 100, 'pattern': r'^[^@\r\n]+@[^\r\n]+$'},
        'unit': {'type': 'string', 'enum': ['mm', 'deg', 'count']},
        'minimum': {'type': 'number', 'minimum': -1e12, 'maximum': 1e12},
        'maximum': {'type': 'number', 'minimum': -1e12, 'maximum': 1e12},
    }, 'required': ['name', 'unit', 'minimum', 'maximum'],
}


def finite(value):
    return type(value) in (int, float) and math.isfinite(value)


def validate_measurement(rule):
    measurement = rule.get('measurement')
    if rule['checker'] not in CHECKERS:
        if measurement is not None:
            raise ValueError('measurement is only valid for dimension checkers.')
        return
    if not isinstance(measurement, dict):
        raise ValueError('Dimension checks require measurement name, unit, minimum and maximum.')
    lo, hi = measurement['minimum'], measurement['maximum']
    if not finite(lo) or not finite(hi) or lo > hi:
        raise ValueError('Dimension bounds must be finite with minimum <= maximum.')
    if measurement['unit'] == 'count':
        if rule['checker'] == 'dimension_tolerance_range' or lo < 0 or int(lo) != lo or int(hi) != hi:
            raise ValueError('Counts require nonnegative integer bounds and no tolerance envelope.')


def measurement_label(rule):
    m = rule.get('measurement')
    if not m:
        return ''
    label = '模型尺寸值' if rule['checker'] == 'dimension_range' else '标注公差包络'
    return f"；{label}={m['name']} [{m['minimum']}, {m['maximum']}] {m['unit']}（闭区间）"


def evaluate_dimension(rule, data, target):
    m = rule['measurement']
    expected = dict(m, method=rule['checker'])
    def result(status, reason, actual=None):
        return dict(status=status, reason=reason, actual=actual, expected=expected)
    rows = data.get('dimensions')
    if (data.get('doc') != target or data.get('complete') is not True or data.get('truncated')
            or data.get('warnings') or data.get('incompleteReads')
            or not isinstance(rows, list) or type(data.get('total')) is not int
            or data['total'] != len(rows) or not all(isinstance(r, dict) for r in rows)):
        return result('UNKNOWN', '尺寸列表不完整、执行器版本过旧或取数异常。')
    matches = [row for row in rows if isinstance(row.get('name'), str)
               and row['name'].casefold() == m['name'].casefold()]
    if len(matches) != 1 or matches[0]['name'] != m['name']:
        return result('UNKNOWN', '尺寸名称缺失或不唯一；不按近似名称替换检查对象。')
    row = matches[0]
    value = row.get('value')
    if row.get('unit') != m['unit'] or not finite(value):
        return result('UNKNOWN', '尺寸单位不匹配或返回值非有限数。')
    if m['unit'] == 'count' and (value < 0 or int(value) != value):
        return result('UNKNOWN', '数量尺寸返回值不是非负整数。')
    actual = {'name': m['name'], 'value': value, 'unit': m['unit'], 'driven': row.get('driven') is True}
    low = high = Decimal(str(value))
    if rule['checker'] == 'dimension_tolerance_range':
        tol = row.get('tolerance')
        if not isinstance(tol, dict) or tol.get('type') not in {'bilateral', 'symmetric', 'limit'}:
            return result('UNKNOWN', '需要可读取的双向、对称或极限尺寸公差；缺失及配合代号不推算。', actual)
        upper, lower = tol.get('upper'), tol.get('lower')
        if not finite(upper) or not finite(lower) or lower > upper:
            return result('UNKNOWN', '公差上下偏差缺失、非有限或顺序异常。', actual)
        if tol['type'] == 'symmetric' and (upper < 0 or lower != -upper):
            return result('UNKNOWN', '对称公差的上下偏差不一致。', actual)
        low += Decimal(str(lower))
        high += Decimal(str(upper))
        actual.update(tolerance=tol, minimum=float(low), maximum=float(high))
    passed = Decimal(str(m['minimum'])) <= low and high <= Decimal(str(m['maximum']))
    reason = ('按闭区间比较 CAD 标注的完整公差包络；不代表实物检验或公差链验算。'
              if rule['checker'] == 'dimension_tolerance_range' else
              '读取已确认名称的模型尺寸值；不证明该尺寸对应最终实体上的目标距离。')
    return result('PASS' if passed else 'FAIL', reason, actual)
