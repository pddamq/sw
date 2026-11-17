"""Reproduce the pc_case_atx discovery/repair chain without a live CAD process."""
import json
from types import SimpleNamespace as NS

import pytest

from backend.agent import CadAgent, merge_discovered, select_tools
from backend.config import SettingsStore
from backend.diagnostics import DiagnosticRecorder, export_diagnostics
from backend.execution import DecisionStopped, ExecutionControl
from backend.knowledge import KnowledgeBase
from backend.storage import Database
from test_agent import FakeBridge, FakeClient, chunk
from test_protocol_recovery_sdk import call_deltas, mock_client, stream_response


class CaseBridge(FakeBridge):
    tools = FakeBridge.tools + [
        {'name': 'doc_activate', 'description': 'Choose the target document',
         'inputSchema': {'type': 'object', 'properties': {
             'doc': {'type': 'string'}, 'allowEdit': {'type': 'boolean'}},
             'required': ['doc'], 'additionalProperties': False}},
        {'name': 'sketch_info', 'description': 'Inspect sketch entities',
         'annotations': {'readOnlyHint': True},
         'inputSchema': {'type': 'object', 'properties': {
             'doc': {'type': 'string'}, 'sketch': {'type': 'string'}},
             'required': ['doc', 'sketch'], 'additionalProperties': False}},
    ]


def wire_call(name, args, call_id, index=0):
    return {'index': index, 'id': call_id, 'type': 'function',
            'function': {'name': name, 'arguments': json.dumps(args)}}


def advertised(request):
    return {tool['function']['name'] for tool in request.get('tools', [])}


@pytest.mark.parametrize('approved', [True, False])
async def test_sdk_missing_sketch_discovery_preserves_activation_and_ui_approval(tmp_path, monkeypatch, approved):
    import backend.agent as module
    monkeypatch.setenv('FORMA_API_KEY', 'fixture')
    db = Database(tmp_path / 'case.db')
    settings = SettingsStore(tmp_path / 'settings.json')
    sid = db.create_session()['id']
    run = db.create_run(sid, 'Improve pc_case_atx')
    bridge, requests, events = CaseBridge(), [], []
    activate = {'doc': 'pc_case_atx', 'allowEdit': True,
                '_forma_purpose': 'Enable editing the requested case document.'}
    inspect = {'doc': 'pc_case_atx', 'sketch': '\u8349\u56fe9'}

    def handler(request):
        body = json.loads(request.content)
        requests.append(body)
        turn = len(requests)
        if turn == 1:
            return stream_response(call_deltas('discover_tools', '{"query":"doc_activate"}', 'discover_activate'), 'tool_calls')
        if turn == 2:
            assert 'doc_activate' in advertised(body)
            assert 'sketch_info' not in advertised(body)
            return stream_response([{'reasoning_content': 'Inspect before modifying.', 'tool_calls': [
                wire_call('doc_activate', activate, 'rejected_activate'),
                wire_call('sketch_info', inspect, 'rejected_inspect', 1)]}], 'tool_calls')
        if turn == 3:
            assert not any(name == 'doc_activate' for name, _ in bridge.calls)
            assert not any(event['type'] == 'approval_required' for event in events)
            assert body['tool_choice'] == 'auto'
            serialized = json.dumps(body['messages'])
            assert 'unavailable_batch' in serialized
            assert 'unregistered_tool' in serialized
            assert 'sketch_info' not in advertised(body)
            return stream_response(call_deltas('discover_tools', '{"query":"sketch_info"}', 'discover_inspect'), 'tool_calls')
        if turn == 4:
            assert {'doc_activate', 'sketch_info'} <= advertised(body)
            assert body['tool_choice'] == 'auto'
            assert 'request_clarification' in advertised(body)
            assert 'OUTPUT VALIDATION ERROR:' not in json.dumps(body['messages'])
            return stream_response([{'tool_calls': [
                wire_call('doc_activate', activate, 'valid_activate'),
                wire_call('sketch_info', inspect, 'valid_inspect', 1)]}], 'tool_calls')
        assert turn == 5
        return stream_response([{'content': 'Sketch inspected; no geometry changes made.'}])

    mock_client(monkeypatch, module, handler)
    agent = CadAgent(db, settings, bridge, KnowledgeBase(db, settings))
    agent.diagnostic = DiagnosticRecorder(db, sid)

    async def emit(event):
        events.append(event)
        if event['type'] == 'approval_required':
            assert event['approval']['name'] == 'doc_activate'
            assert not any(name == 'doc_activate' for name, _ in bridge.calls)
            agent.control.decide(event['approval']['id'], approved)

    agent.control = ExecutionControl(db, bridge, run['id'], emit)
    if approved:
        await agent.run(sid, 'Improve pc_case_atx', emit)
        assert [(name, args) for name, args in bridge.calls if name != 'sw_status'] == [
            ('doc_activate', {'doc': 'pc_case_atx', 'allowEdit': True}), ('sketch_info', inspect)]
    else:
        with pytest.raises(DecisionStopped):
            await agent.run(sid, 'Improve pc_case_atx', emit)
        assert all(name == 'sw_status' for name, _ in bridge.calls)
    assert len(requests) == (5 if approved else 4)
    assert len([e for e in events if e['type'] == 'approval_required']) == 1
    assert not any(e.get('status') == 'rejected' for e in events)
    assert not any(e.get('status') == 'interrupted' for e in events)
    records = export_diagnostics(db, sid)['records']
    snapshots = [r['data']['request_snapshot'] for r in records if r['kind'] == 'model_request']
    assert 'sketch_info' not in snapshots[1]['tool_names']
    assert 'sketch_info' in snapshots[3]['tool_names']


def test_exact_names_outrank_fuzzy_matches_and_disabled_vba_stays_hidden():
    noise = [{'name': f'sketch_info_noise_{i}', 'description': 'sketch info details'} for i in range(30)]
    target = {'name': 'sketch_info', 'description': 'Inspect entities'}
    tools = noise + [target, {'name': 'vba_run'}]
    assert select_tools(tools, 'sketch_info', limit=1) == [target]
    assert select_tools(tools, 'vba_run') == []
    assert select_tools(tools, 'vba_run', allow_vba=True)[0]['name'] == 'vba_run'
    assert target not in select_tools([target], 'sketch_info_noise')


def test_discovery_retains_recent_tools_but_stays_bounded():
    base = CaseBridge.tools[0]
    selected = {'sw_status': base, 'doc_activate': CaseBridge.tools[1]}
    merge_discovered(selected, [CaseBridge.tools[2]])
    assert set(selected) == {'sw_status', 'doc_activate', 'sketch_info'}
    before = dict(selected)
    merge_discovered(selected, [])
    assert selected == before
    for i in range(40):
        merge_discovered(selected, [{'name': f'tool_{i}'}])
        assert len(selected) <= 15
        assert selected['sw_status'] == base
        assert f'tool_{i}' in selected
    assert 'tool_0' not in selected


@pytest.mark.parametrize('invalid_retry', [None, 'mixed', 'textual', 'blank'])
async def test_clarification_is_explicit_terminal_and_never_runs_cad(tmp_path, monkeypatch, invalid_retry):
    import backend.agent as module
    monkeypatch.setenv('FORMA_API_KEY', 'fixture')
    db = Database(tmp_path / 'clarify.db')
    settings = SettingsStore(tmp_path / 'settings.json')
    sid = db.create_session()['id']
    db.add_message(sid, 'user', 'Improve the case')
    question = 'What dimensions should the mounting holes use?'

    def call(name, args, id, index=0):
        return NS(index=index, id=id, function=NS(name=name, arguments=json.dumps(args)))

    clarification = {'question': question, 'reason': 'Mounting hole dimensions are missing.'}
    responses = [[chunk(calls=[call('sketch_info', {}, 'unregistered')])]]
    if invalid_retry == 'mixed':
        responses.append([chunk(calls=[call('sw_status', {}, 'must_not_run'),
            call('request_clarification', clarification, 'mixed_question', 1)])])
    elif invalid_retry == 'textual':
        responses.append([chunk(calls=[call('request_clarification', {
            **clarification, 'question': '<tool_call>sw_status</tool_call>'}, 'textual')])])
    elif invalid_retry == 'blank':
        responses.append([chunk(calls=[call('request_clarification', {
            **clarification, 'question': '   '}, 'blank')])])
    responses.append([chunk(calls=[call('request_clarification', clarification, 'question')])])
    captures, events = [], []
    monkeypatch.setattr(module, 'AsyncOpenAI', lambda **kw: FakeClient(responses, captures))
    bridge = CaseBridge()
    agent = CadAgent(db, settings, bridge, KnowledgeBase(db, settings))
    agent.diagnostic = DiagnosticRecorder(db, sid)

    async def emit(event):
        events.append(event)

    await agent.run(sid, 'Improve the case', emit)
    assert bridge.calls == [('sw_status', {})] * 2
    assert len(captures) == (2 if invalid_retry is None else 3)
    assert all(c.get('tool_choice') in {'auto', 'required'} for c in captures[1:])
    assert db.messages(sid)[-1]['content'] == question
    assert [e['content'] for e in events if e['type'] == 'answer'] == [question]
    assert not any(e['type'] == 'approval_required' for e in events)
    records = export_diagnostics(db, sid)['records']
    assert len([r for r in records if r['kind'] == 'clarification_requested']) == 1
