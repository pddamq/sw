import copy
import json
from types import SimpleNamespace as NS

import pytest
from jsonschema import Draft202012Validator, ValidationError

from backend.agent import CadAgent
from backend.config import SettingsStore
from backend.knowledge import KnowledgeBase
from backend.tool_purpose import PURPOSE_FIELD, explained_tool, split_purpose
from test_agent import FakeClient, chunk
from test_execution_control import environment


def test_model_schema_adds_purpose_without_changing_executor_schema():
    tool = {'name': 'doc_save', 'inputSchema': {'type': 'object', 'properties': {'path': {'type': 'string'}},
                                             'required': ['path'], 'additionalProperties': False}}
    original = copy.deepcopy(tool)
    model_tool = explained_tool(tool)
    assert tool == original
    assert PURPOSE_FIELD in model_tool['inputSchema']['required']
    args = {'path': 'C:/parts/plate.sldprt', PURPOSE_FIELD: 'Save the current plate to preserve the changes.'}
    Draft202012Validator(model_tool['inputSchema']).validate(args)
    clean, purpose = split_purpose(args)
    Draft202012Validator(tool['inputSchema']).validate(clean)
    assert PURPOSE_FIELD in args and purpose
    with pytest.raises(ValidationError):
        Draft202012Validator(model_tool['inputSchema']).validate(clean)


@pytest.mark.parametrize('purpose', ['', '  \n ', 123, {}, 'x' * 601])
def test_invalid_purpose_is_rejected(purpose):
    with pytest.raises(ValueError):
        split_purpose({PURPOSE_FIELD: purpose})


@pytest.mark.parametrize('name,arguments,annotations', [
    ('doc_save', {'path': 'C:/parts/plate.sldprt'}, {'destructiveHint': False}),
    ('doc_activate', {'allowEdit': True}, {'destructiveHint': False}),
    ('custom_modify', {}, {}),
])
async def test_graph_requests_explanation_then_confirms_exact_call(environment, tmp_path, monkeypatch,
                                                                name, arguments, annotations):
    import backend.agent as module
    db, session, run, bridge, events, control = environment
    monkeypatch.setenv('FORMA_API_KEY', 'fixture-key')
    properties = {key: {'type': 'boolean' if isinstance(value, bool) else 'string'} for key, value in arguments.items()}
    target = {'name': name, 'description': name, 'annotations': annotations,
              'inputSchema': {'type': 'object', 'properties': properties, 'additionalProperties': False}}
    bridge.tools = [t for t in bridge.tools if t['name'] != name] + [target]
    purpose = '修改当前零件以完成用户要求的步骤；请核对目标文档。'
    def call(id_, args):
        return NS(index=0, id=id_, function=NS(name=name, arguments=json.dumps(args)))
    captures = []
    responses = [[chunk(calls=[call('missing', arguments)])],
                 [chunk(calls=[call('explained', {**arguments, PURPOSE_FIELD: purpose})])],
                 [chunk('Fixture complete.')]]
    monkeypatch.setattr(module, 'AsyncOpenAI', lambda **kw: FakeClient(responses, captures))
    async def emit(event):
        events.append(event)
        if event['type'] == 'approval_required':
            assert not any(n == name for n, _ in bridge.calls)
            assert event['approval']['purpose'] == purpose
            assert event['approval']['arguments'] == arguments
            assert event['approval']['reason']
            control.decide(event['approval']['id'], True)
    control.emit = emit
    settings = SettingsStore(tmp_path / 'settings.json')
    agent = CadAgent(db, settings, bridge, KnowledgeBase(db, settings))
    agent.control = control
    await agent.run(session['id'], name, emit)
    assert [(n, a) for n, a in bridge.calls if n == name] == [(name, arguments)]
    assert len([e for e in events if e['type'] == 'approval_required']) == 1
    errors = [e for e in events if e['type'] == 'tool_result' and e['is_error']]
    assert errors == []
    assert any(e['type'] == 'status' and '协议校验' in e['message'] for e in events)
    assert 'OUTPUT VALIDATION ERROR' in captures[1]['messages'][-1]['content']
    assert next(e for e in events if e['type'] == 'tool_start' and e['name'] == name)['purpose'] == purpose
    assert next(c for c in db.checkpoints(run['id']) if c['name'] == name)['arguments'] == arguments
    advertised = next(t for t in captures[0]['tools'] if t['function']['name'] == name)
    assert PURPOSE_FIELD in advertised['function']['parameters']['properties']


async def test_discovery_returns_model_explanation_schema(environment, tmp_path, monkeypatch):
    import backend.agent as module
    db, session, _, bridge, events, control = environment
    monkeypatch.setenv('FORMA_API_KEY', 'fixture-key')
    discovery = NS(index=0, id='discover', function=NS(name='discover_tools', arguments='{"query":"doc_save"}'))
    captures = []
    monkeypatch.setattr(module, 'AsyncOpenAI', lambda **kw: FakeClient(
        [[chunk(calls=[discovery])], [chunk('Found save tool.')]], captures))
    settings = SettingsStore(tmp_path / 'settings.json')
    agent = CadAgent(db, settings, bridge, KnowledgeBase(db, settings))
    agent.control = control
    async def emit(event):
        events.append(event)
    await agent.run(session['id'], 'inspect', emit)
    result = next(e for e in events if e['type'] == 'tool_result' and e['name'] == 'discover_tools')
    save = next(t for t in json.loads(result['content']) if t['name'] == 'doc_save')
    assert PURPOSE_FIELD in save['inputSchema']['required']
    assert 'inputSchema' not in next(t for t in bridge.tools if t['name'] == 'doc_save')
