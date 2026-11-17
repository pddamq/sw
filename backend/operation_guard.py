"""Argument-sensitive confirmation rules for the pinned SOLIDWORKS tool surface."""
from __future__ import annotations

# Routine modeling edits are covered by the task instruction. These operations additionally
# require a one-time UI decision; model-supplied confirm/overwrite flags are not user approval.
ALWAYS_CONFIRM = {
    'doc_save': '保存会写入文件；请核对保存模式、目标文档和路径。',
    'doc_close': '关闭文档可能保存或丢弃现有修改，请核对目标。',
    'doc_undo': '撤销会改变当前模型状态，请核对撤销次数。',
    'dialog_respond': '即将响应 SOLIDWORKS 对话框，请核对窗口和按钮。',
    'vba_run': '宏可修改模型和本机文件，请核对本次完整宏参数。',
    'feature_delete': '删除特征可能同时影响依赖特征，请核对删除范围。',
    'body_delete': '删除或仅保留指定实体会改变模型，请核对实体范围。',
    'sketch_delete': '即将删除草图实体，请核对目标。',
    'sketch_trim': '即将裁剪草图实体，请核对目标。',
    'prop_delete': '即将删除自定义属性，请核对属性范围。',
    'equation_delete': '即将删除方程，请核对目标。',
    'config_delete': '即将删除配置，请核对目标。',
    'sim_delete': '即将删除仿真研究及相关结果，请核对目标。',
    'asm_replace': '即将替换装配组件，请核对文件及实例范围。',
}


def confirmation_reason(name: str, arguments: dict) -> str | None:
    if name in ALWAYS_CONFIRM:
        return ALWAYS_CONFIRM[name]
    if name == 'sw_connect' and arguments.get('launch') is True:
        return '即将启动 SOLIDWORKS，可能占用许可席位。'
    if name == 'doc_activate' and arguments.get('allowEdit') is True:
        return '即将允许执行器编辑已有用户文档，请核对目标文档。'
    if arguments.get('overwrite') is True:
        return '本次调用允许覆盖现有文件或数据，请核对目标路径。'
    if arguments.get('discard') is True or arguments.get('confirm') is True:
        return '本次调用包含丢弃或确认标记，需要核对具体操作。'
    if name == 'asm_structure' and str(arguments.get('kind', '')).strip().casefold() == 'dissolve':
        return '即将解散子装配并移除其结构，请核对组件范围。'
    if name == 'face_edit' and str(arguments.get('op', '')).strip().casefold() == 'delete':
        return '即将删除模型面，请核对目标与修补选项。'
    return None


def evidence_kind(name: str) -> str:
    if name in {'sw_status', 'doc_list', 'model_info', 'feature_list', 'feature_info', 'dialog_list'}:
        return 'state'
    if name == 'doc_rebuild':
        return 'rebuild'
    if name in {'model_measure', 'model_mass', 'dimension_list', 'model_section'}:
        return 'measurement'
    if name in {'model_review', 'model_check', 'model_compare', 'asm_interference'}:
        return 'review'
    if name in {'doc_save', 'model_export', 'model_export_all', 'doc_pack', 'body_save', 'sheetmetal_export_flat'}:
        return 'saved_file'
    return 'other'
