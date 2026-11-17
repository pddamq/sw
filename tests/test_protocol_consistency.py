"""Offline regressions for protocol-specific prompts and request invariants."""
import copy
import json
from types import SimpleNamespace as NS

import pytest

from backend.agent import CadAgent, DISCOVER
from backend.config import Settings, SettingsStore
from backend.knowledge import KnowledgeBase
from backend.model_history import HistoryValidationError, validate_history
from backend.model_transport import HISTORY_FIELD, ModelClient, claude_request, responses_request
from backend.model_endpoint import client_options
from backend.output_guard import ProtocolViolation, repair_guidance, validate_response
from backend.prompting import protocol_examples
from backend.storage import Database
from backend.tool_snapshot import ToolSnapshot
from test_agent import FakeBridge, FakeClient, chunk
from test_native_model_protocols import install_transport, sse, claude_rows, responses_rows


def call(id='a', name='inspect', arguments='{}'):
    return {'id': id, 'type': 'function', 'function': {'name': name, 'arguments': arguments}}


def exchange():
    return [{'role': 'user', 'content': 'Inspect'},
            {'role': 'assistant', 'content': None, 'tool_calls': [call()]},
            {'role': 'tool', 'tool_call_id': 'a', 'content': 'Observed'}]


@pytest.mark.parametrize('protocol', ['chat', 'responses', 'claude'])
def test_examples_match_selected_wire_protocol(protocol):
    text = protocol_examples([DISCOVER], protocol)
    assert 'native' in text and 'discover_tools' in text
    assert '"tool_calls":' not in text and '<|DSML|' not in text
    if protocol == 'chat':
        assert 'tool_calls' in text and 'function.arguments' in text
    elif protocol == 'claude':
        assert 'tool_use' in text
        assert 'tool_calls' not in text and 'function.arguments' not in text
    else:
        assert 'function_call' in text
        assert 'tool_calls' not in text and 'function.arguments' not in text


@pytest.mark.parametrize('protocol', ['chat', 'responses', 'claude'])
@pytest.mark.parametrize('fault', ['orphan', 'missing', 'duplicate_result', 'duplicate_call', 'interleaved', 'wrong_id'])
def test_request_history_rejects_incomplete_or_ambiguous_pairs(protocol, fault):
    messages = exchange()
    if fault == 'orphan':
        del messages[1]
    elif fault == 'missing':
        messages.pop()
    elif fault == 'duplicate_result':
        messages.append(copy.deepcopy(messages[-1]))
    elif fault == 'duplicate_call':
        messages[1]['tool_calls'].append(call())
    elif fault == 'interleaved':
        messages.insert(2, {'role': 'system', 'content': 'Update'})
    else:
        messages[-1]['tool_call_id'] = 'wrong'
    with pytest.raises(HistoryValidationError):
        validate_history(messages, protocol, 'fixture')


@pytest.mark.parametrize('protocol', ['responses', 'claude'])
@pytest.mark.parametrize('fault', ['name', 'arguments', 'id', 'extra_call', 'text', 'boolean'])
def test_native_replay_must_match_validated_turn(protocol, fault):
    messages = exchange()
    messages[1]['tool_calls'][0]['function']['arguments'] = '{"value":1}'
    item = ({'type': 'tool_use', 'id': 'a', 'name': 'inspect', 'input': {'value': 1}}
            if protocol == 'claude' else
            {'type': 'function_call', 'call_id': 'a', 'name': 'inspect', 'arguments': '{"value":1}'})
    items = [item]
    if fault == 'name':
        item['name'] = 'invented'
    elif fault == 'arguments':
        item['input' if protocol == 'claude' else 'arguments'] = {'value': 2} if protocol == 'claude' else '{"value":2}'
    elif fault == 'id':
        item['id' if protocol == 'claude' else 'call_id'] = 'different'
    elif fault == 'extra_call':
        items.append(copy.deepcopy(item))
    elif fault == 'boolean':
        item['input' if protocol == 'claude' else 'arguments'] = {'value': True} if protocol == 'claude' else '{"value":true}'
    else:
        items.append({'type': 'text', 'text': 'Unvalidated text'} if protocol == 'claude' else
                     {'type': 'message', 'content': [{'type': 'output_text', 'text': 'Unvalidated text'}]})
    messages[1][HISTORY_FIELD] = {'protocol': protocol, 'model': 'fixture', 'items': items}
    with pytest.raises(HistoryValidationError):
        validate_history(messages, protocol, 'fixture')
    # Foreign model replay is discarded by the serializer, never trusted here.
    validate_history(messages, protocol, 'other-model')


def test_complete_batch_and_image_feedback_preserve_pairs_and_error_flag():
    messages = exchange()
    messages[1]['tool_calls'].append(call('b'))
    messages[2]['is_error'] = True
    messages.append({'role': 'tool', 'tool_call_id': 'b', 'content': 'Second'})
    messages.append({'role': 'user', 'content': [{'type': 'image_url', 'image_url': {'url': 'data:image/png;base64,YQ=='}}]})
    original = copy.deepcopy(messages)
    options = {'model': 'fixture', 'max_tokens': 100, 'messages': messages}
    body = claude_request(options)
    blocks = body['messages'][-1]['content']
    assert [b['type'] for b in blocks] == ['tool_result', 'tool_result', 'image']
    assert blocks[0]['is_error'] is True
    responses_request(options)
    assert messages == original


@pytest.mark.parametrize('protocol', ['chat', 'responses', 'claude'])
async def test_invalid_history_stops_before_provider_request(monkeypatch, protocol):
    def forbidden(*args, **kwargs):
        pytest.fail('Invalid history reached a provider')
    install_transport(monkeypatch, forbidden)
    sdk = NS(chat=NS(completions=NS(create=forbidden)))
    client = ModelClient(Settings(protocol=protocol), 'fixture', chat_factory=lambda **kw: sdk,
                         chat_options=client_options)
    with pytest.raises(HistoryValidationError):
        await client.create(model='fixture', messages=exchange()[:-1], max_tokens=100)


def test_snapshot_is_detached_tracks_changes_and_checks_live_definitions():
    bridge = FakeBridge()
    bridge.tools = copy.deepcopy(bridge.tools)
    schema = {'type': 'function', 'function': {'name': 'sw_status', 'parameters': bridge.tools[0]['inputSchema']}}
    first = ToolSnapshot([schema], bridge.tools)
    changed = first.schemas
    changed[0]['function']['parameters']['properties']['new'] = {'type': 'string'}
    assert first.schemas == [schema]
    first.check_live('sw_status', bridge)
    bridge.tools[0]['inputSchema']['required'] = ['new']
    with pytest.raises(ProtocolViolation, match='unavailable_tool'):
        first.check_live('sw_status', bridge)
    second = ToolSnapshot([schema, DISCOVER], bridge.tools)
    delta = second.changes(first)
    assert delta['changed'] == ['sw_status'] and delta['added'] == ['discover_tools']
    assert ToolSnapshot([]).changes(second)['removed'] == ['sw_status', 'discover_tools']


def test_unknown_tool_feedback_never_advertises_unavailable_discovery():
    with pytest.raises(ProtocolViolation) as error:
        validate_response('', [call(name='invented')], [])
    assert 'Call discover_tools' not in str(error.value)
    assert 'No discovery tool' in repair_guidance(error.value, [])
    assert 'not_executed' in repair_guidance(error.value, [DISCOVER])


async def test_changed_registry_rejects_response_then_refreshes_next_request(tmp_path, monkeypatch):
    import backend.agent as module
    monkeypatch.setenv('FORMA_API_KEY', 'fixture')
    settings = SettingsStore(tmp_path / 'settings.json')
    db = Database(tmp_path / 'registry.db')
    sid = db.create_session()['id']
    db.add_message(sid, 'user', 'Inspect status')
    bridge = FakeBridge()
    bridge.tools = copy.deepcopy(bridge.tools)
    captures, events = [], []

    class ChangingClient(FakeClient):
        async def create(self, **kwargs):
            response = await super().create(**kwargs)
            if len(captures) == 1:
                bridge.tools[0]['inputSchema']['properties']['detail'] = {'type': 'boolean'}
            return response

    responses = [[chunk(calls=[NS(index=0, id='rejected', function=NS(name='sw_status', arguments='{}'))])],
                 [chunk(calls=[NS(index=0, id='accepted', function=NS(name='sw_status', arguments='{}'))])],
                 [chunk('Status checked.')]]
    monkeypatch.setattr(module, 'AsyncOpenAI', lambda **kw: ChangingClient(responses, captures))
    agent = CadAgent(db, settings, bridge, KnowledgeBase(db, settings))
    async def emit(event):
        events.append(event)
    await agent.run(sid, 'Inspect status', emit)
    assert 'detail' not in captures[0]['tools'][1]['function']['parameters']['properties']
    assert 'detail' in captures[1]['tools'][1]['function']['parameters']['properties']
    assert '"changed": ["sw_status"]' in captures[1]['messages'][0]['content']
    assert 'not_executed' in captures[1]['messages'][-1]['content']
    assert len([e for e in events if e['type'] == 'tool_start' and e.get('call_id')]) == 1
    assert bridge.calls == [('sw_status', {})] * 3


@pytest.mark.parametrize('protocol', ['responses', 'claude'])
async def test_native_repair_prompts_never_reintroduce_chat_envelopes(monkeypatch, tmp_path, protocol):
    rows = claude_rows if protocol == 'claude' else responses_rows
    requests, events = [], []
    def handler(request):
        body = json.loads(request.content)
        requests.append(body)
        if protocol == 'claude':
            system = '\n'.join(block['text'] for block in body['system'])
        else:
            system = '\n'.join(block['text'] for item in body['input'] if item.get('role') == 'system'
                               for block in item['content'])
        assert 'tool_calls' not in system and 'function.arguments' not in system
        if len(requests) == 1:
            return sse(rows(call={'call_id': 'bad', 'name': 'invented', 'arguments': '{}'}))
        if len(requests) == 2:
            # dsh-style UNKNOWN_TOOL is a per-call result, not a whole-response repair.
            assert 'unregistered_tool' in json.dumps(body)
            return sse(rows(call={'call_id': 'good', 'name': 'knowledge_list', 'arguments': '{}'}, thinking=True))
        return sse(rows(text='Library verified.'))
    install_transport(monkeypatch, handler)
    monkeypatch.setenv('FORMA_API_KEY', 'fixture')
    settings = SettingsStore(tmp_path / 'settings.json')
    settings.value = Settings(protocol=protocol, base_url='https://fixture.test/v1')
    db = Database(tmp_path / 'native.db')
    sid = db.create_session()['id']
    db.add_document('fixture.txt', ['Units are mm.'], None, '')
    db.add_message(sid, 'user', 'List the library')
    bridge = FakeBridge()
    bridge.running = False
    async def emit(event):
        events.append(event)
    await CadAgent(db, settings, bridge, KnowledgeBase(db, settings)).run(sid, 'List the library', emit)
    assert len(requests) == 3
    results = [e for e in events if e['type'] == 'tool_result']
    assert len(results) == 2
    unknown = [e for e in results if e.get('call_id') == 'bad'][0]
    assert json.loads(unknown['content'])['category'] == 'unregistered_tool'
    assert db.messages(sid)[-1]['content'] == 'Library verified.'


async def test_pre_dispatch_registry_change_does_not_blacklist_unexecuted_calls(tmp_path, monkeypatch):
    import backend.agent as module
    monkeypatch.setenv('FORMA_API_KEY', 'fixture')
    settings = SettingsStore(tmp_path / 'settings.json')
    db = Database(tmp_path / 'dispatch.db')
    sid = db.create_session()['id']
    db.add_message(sid, 'user', 'Inspect status')
    bridge = FakeBridge()
    bridge.tools = copy.deepcopy(bridge.tools)
    captures, events = [], []
    def wire(id, index=0):
        return NS(index=index, id=id, function=NS(name='sw_status', arguments='{}'))
    responses = [[chunk(calls=[wire('stale'), wire('skipped', 1)])],
                 [chunk(calls=[wire('fresh')])], [chunk('Checked.')]]
    monkeypatch.setattr(module, 'AsyncOpenAI', lambda **kw: FakeClient(responses, captures))
    async def emit(event):
        events.append(event)
        if event['type'] == 'tool_start' and event.get('call_id') == 'stale':
            bridge.tools[0]['inputSchema']['properties']['detail'] = {'type': 'boolean'}
    await CadAgent(db, settings, bridge, KnowledgeBase(db, settings)).run(sid, 'Inspect status', emit)
    results = [e for e in events if e['type'] == 'tool_result' and e.get('call_id')]
    assert [e['call_id'] for e in results] == ['stale', 'skipped', 'fresh']
    assert [e['is_error'] for e in results] == [True, True, False]
    assert all(json.loads(e['content'])['outcome'] == 'not_executed' for e in results[:2])
    assert bridge.calls == [('sw_status', {})] * 3
    assert len(captures) == 3


def test_historical_calls_need_not_remain_in_current_registry():
    messages = exchange()
    options = {'model': 'fixture', 'max_tokens': 100, 'messages': messages, 'tools': [DISCOVER]}
    assert responses_request(options)['input'][-1]['type'] == 'function_call_output'
    assert claude_request(options)['messages'][-1]['content'][0]['type'] == 'tool_result'


def test_schema_feedback_includes_expected_type_without_rejected_value():
    schema = {'type': 'function', 'function': {'name': 'inspect', 'parameters': {
        'type': 'object', 'properties': {'depth': {'type': 'number'}}, 'required': ['depth']}}}
    with pytest.raises(ProtocolViolation) as error:
        validate_response('', [call(arguments='{"depth":"untrusted-payload"}')], [schema])
    assert 'at depth violates type' in str(error.value)
    assert 'expected "number"' in str(error.value)
    assert 'untrusted-payload' not in str(error.value)


def test_history_validation_and_atomic_compaction_preserve_complete_exchanges():
    from backend.context_policy import compression_plan
    messages = [{'role': 'system', 'content': 'Rules'}] + exchange()
    messages += [{'role': 'user', 'content': 'Continue'}]
    messages += [{'role': 'assistant', 'content': None, 'tool_calls': [call('b')]},
                 {'role': 'tool', 'tool_call_id': 'b', 'content': 'Second'}]
    removed, retained = compression_plan(messages, [], 1, '', protected_user_index=4)
    assert removed == [1, 2, 3]
    for protocol in ('chat', 'responses', 'claude'):
        validate_history(retained, protocol, 'fixture')
    assert retained[-1]['tool_call_id'] == 'b'
