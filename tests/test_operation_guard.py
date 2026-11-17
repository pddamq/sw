import pytest

from backend.operation_guard import confirmation_reason, evidence_kind


@pytest.mark.parametrize('name,args', [
    ('doc_save', {}), ('doc_save', {'mode': 'inPlace', 'overwrite': False}),
    ('doc_close', {}), ('doc_close', {'save': True}), ('doc_close', {'discard': True}),
    ('doc_activate', {'doc': 'D1', 'allowEdit': True}), ('sw_connect', {'launch': True}),
    ('dialog_respond', {'handle': '0x123', 'button': 'No', 'confirm': False}),
    ('vba_run', {'code': 'fixture'}), ('feature_delete', {'features': ['Cut1']}),
    ('body_delete', {'mode': 'keep_only', 'bodies': [{'id': 'B1'}]}),
    ('model_export', {'format': 'step', 'overwrite': True}), ('sim_delete', {}),
    ('face_edit', {'op': 'DELETE'}), ('asm_structure', {'kind': 'dissolve'}),
    ('view_screenshot', {'saveTo': 'image.png', 'overwrite': True}),
])
def test_sensitive_argument_variants_need_confirmation(name, args):
    assert confirmation_reason(name, args)


@pytest.mark.parametrize('name,args', [
    ('sw_status', {}), ('sw_connect', {'launch': False}),
    ('doc_activate', {'doc': 'D1', 'allowEdit': False}),
    ('feature_extrude', {'depthMm': 10}), ('model_measure', {}),
    ('view_screenshot', {}), ('face_edit', {'op': 'offset'}),
])
def test_routine_calls_do_not_request_extra_confirmation(name, args):
    assert confirmation_reason(name, args) is None


@pytest.mark.parametrize('name,kind', [('sw_status', 'state'), ('doc_rebuild', 'rebuild'),
    ('model_measure', 'measurement'), ('model_check', 'review'), ('doc_save', 'saved_file'),
    ('feature_extrude', 'other')])
def test_evidence_categories_do_not_depend_on_requested_values(name, kind):
    assert evidence_kind(name) == kind
