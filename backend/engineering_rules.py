"""Versioned application checklists, not published manufacturing standards.

These packs identify evidence still required for each process. They deliberately
contain no guessed numerical limits and never promote reference prose to PASS.
"""
from __future__ import annotations

import copy

VERSION = '1.1.0'
PROCESSES = ('concept', 'machining', 'sheet_metal', 'molding', 'casting',
             'welding', 'additive', 'assembly', 'other')
_CHECKLISTS = {
    'concept': [],
    'machining': [('tool_access', '刀具可达性及装夹基准'), ('features', '孔槽、壁厚与机床能力'),
                  ('tolerances', '尺寸、公差及表面质量')],
    'sheet_metal': [('bends', '板厚、折弯半径及孔边距'), ('unfold', '展开、折弯系数及工序干涉'),
                   ('tooling', '折弯模具及设备能力')],
    'molding': [('draft', '拔模、脱模及倒扣'), ('walls', '壁厚、筋及收缩'),
                ('tooling', '分型、浇口及模具能力')],
    'casting': [('draft', '分型、拔模及砂芯'), ('allowances', '壁厚、收缩及加工余量'),
                ('quality', '铸造缺陷及检验要求')],
    'welding': [('joints', '接头、焊缝及焊接工艺'), ('access', '焊枪可达性与装夹'),
                ('distortion', '变形、焊接顺序及检验')],
    'additive': [('orientation', '打印方向、悬垂及支撑'), ('capability', '最小特征及设备材料能力'),
                 ('postprocess', '各向异性、后处理及检验')],
    'assembly': [('interference', '组件干涉及配合约束'), ('path', '装配顺序、装拆路径及工具空间'),
                 ('stackup', '接口、公差链及紧固要求')],
    'other': [('process', '明确工艺、适用标准及验收方法')],
}


def load_rules(process: str) -> dict:
    if process not in PROCESSES:
        raise ValueError('Unknown engineering process.')
    return copy.deepcopy({
        'id': 'forma.' + process, 'version': VERSION, 'process': process,
        'source': 'Forma application checklist v' + VERSION,
        'authority': 'internal_checklist_not_manufacturing_standard',
        'baseline': ['target_identity', 'model_health', 'part_geometry', 'confirmed_material'],
        'supported_checkers': ['solid_body_count', 'material_equals', 'manual', 'dimension_range', 'dimension_tolerance_range'],
        'limitations': 'Manufacturing limits require a sourced project/factory standard and a validated measurement adapter.',
        'requirements': [
            {'id': '_rule_' + process + '_' + key, 'description': description,
             'checker': 'manual', 'expected': '提供项目/工厂标准版本、适用条件、数值和验收证据',
             'source': 'Forma internal ' + process + ' checklist v' + VERSION,
             'reason': '当前仅加载核验清单，尚无经验证的工艺取数与判定适配器。'}
            for key, description in _CHECKLISTS[process]
        ],
    })
