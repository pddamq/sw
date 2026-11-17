"""Whole-response tool validation: no rejected text or partial batches escape."""
import json
from types import SimpleNamespace as NS

import pytest

from backend.agent import CadAgent, DISCOVER
from backend.config import SettingsStore
from backend.knowledge import KnowledgeBase
from backend.output_guard import ProtocolViolation, contains_textual_call, strict_arguments, validate_response
from backend.prompting import protocol_examples
from backend.storage import Database
from test_agent import FakeBridge, FakeClient, chunk


SCHEMAS = [{'type': 'function', 'function': {'name': 'fixture_inspect', 'parameters': {
    'type': 'object', 'properties': {'depth': {'type': 'number'}, 'enabled': {'type': 'boolean'}},
    'required': ['depth'], 'additionalProperties': False}}}]


def native(name='fixture_inspect', arguments='{"depth":15}', id='c1'):
    return {'id': id, 'type': 'function', 'function': {'name': name, 'arguments': arguments}}


@pytest.mark.parametrize('arguments', ['[]', '"{}"', 'null', '{bad}', '{"depth":NaN}',
    '{"depth":Infinity}', '{"depth":1e999}', '{"depth":1,"depth":2}', '{"nested":{"x":1,"x":2}}'])
def test_only_strict_json_objects_are_accepted(arguments):
    with pytest.raises(ProtocolViolation, match='invalid_arguments_json'):
        strict_arguments(arguments)


@pytest.mark.parametrize('arguments', ['{"depth":"15"}', '{"depth":true}', '{}',
                                     '{"depth":15,"enabled":"false"}', '{"depth":15,"bogus":1}'])
def test_schema_types_are_not_coerced(arguments):
    with pytest.raises(ProtocolViolation, match='argument_schema'):
        validate_response('', [native(arguments=arguments)], SCHEMAS)


def test_call_envelope_type_and_name_are_strict():
    for call in [native(name=[]), {**native(), 'type': 'custom'}, {'id': 'c1', 'type': 'function', 'function': None}]:
        with pytest.raises(ProtocolViolation):
            validate_response('', [call], SCHEMAS)


def test_api_argument_string_decodes_to_typed_object():
    validate_response('Inspecting the fixture.', [native(arguments='{"depth":15,"enabled":false}')], SCHEMAS)
    assert strict_arguments('{"depth":15,"enabled":false}') == {'depth': 15, 'enabled': False}


@pytest.mark.parametrize('calls', [[native(name='invented')], [native(id='')], [native(id=' ')],
                                 [native(), native()], [native(id='earlier')]])
def test_unknown_names_and_reused_ids_are_rejected(calls):
    with pytest.raises(ProtocolViolation):
        validate_response('', calls, SCHEMAS, used_ids=['earlier'])


@pytest.mark.parametrize('text', [
    'Prefix <|DSML|invoke name="invented">payload',
    '<tool_call>{"name":"invented","arguments":{}}</tool_call>',
    '<function_calls><invoke name="invented"></invoke></function_calls>',
    '```json\n{"tool_calls":[{"function":{"name":"invented","arguments":"{}"}}]}\n```',
    '{"name":"invented","arguments":{}}',
    '{"function":{"arguments":"{}","name":"invented"}}',
    '{"tool_calls": [',
    'fixture_inspect(depth=15)',
    '`fixture_inspect(depth=15)`',
    '```python\nfixture_inspect(depth=15)\n```',
    'Execute fixture_inspect(depth=15)',
    'Explanation: `<tool_call>{"name":"invented","arguments":{}}</tool_call>`',
    'Example: `{"name":"invented","arguments":{}}`',
])
def test_protocol_text_is_not_an_executable_output(text):
    assert contains_textual_call(text, ['fixture_inspect'])
    with pytest.raises(ProtocolViolation, match='textual_tool_protocol'):
        validate_response(text, [native()], SCHEMAS)


@pytest.mark.parametrize('text', ['2 < 3 and 4 > 1', '<b>Ordinary HTML</b>',
    'The tool_calls field is separate from content.', 'The measurement was 15 mm.',
    '{"width":15,"height":20}', 'DSMLParser is an identifier.',
    'The example `fixture_inspect(depth=15)` describes a parameter; it has not run.',
    '是否调用 `fixture_inspect(depth=15)`？这只是参数说明。'])
def test_normal_explanations_and_dimension_data_remain_valid(text):
    validate_response(text, [], SCHEMAS)


def test_examples_reference_only_current_registered_valid_tools():
    status = {'type': 'function', 'function': {'name': 'sw_status', 'parameters': {
        'type': 'object', 'properties': {}, 'additionalProperties': False}}}
    schemas = [status, DISCOVER]
    text = protocol_examples(schemas)
    assert 'sw_status' in text and 'discover_tools' in text
    assert '"tool_calls":' not in text and '"name":' not in text
    empty = protocol_examples([])
    assert '(none)' in empty
    assert 'example_1' not in empty


@pytest.mark.parametrize('bad_name,bad_args,text', [
    ('sw_status', '{"bogus":true}', 'Rejected claim of success'),
    ('sw_status', '{}', 'Rejected claim <tool_call>bad</tool_call>'),
])
async def test_entire_batch_discarded_then_corrected_before_first_execution(tmp_path, monkeypatch, bad_name, bad_args, text):
    import backend.agent as module
    monkeypatch.setenv('FORMA_API_KEY', 'fixture')
    db = Database(tmp_path / 'guard.db')
    settings = SettingsStore(tmp_path / 'settings.json')
    session = db.create_session()
    db.add_message(session['id'], 'user', 'Inspect')
    captures, events = [], []
    bridge = FakeBridge()
    first = NS(index=0, id='first', function=NS(name='sw_status', arguments='{}'))
    second = NS(index=1, id='bad', function=NS(name=bad_name, arguments=bad_args))
    fixed = NS(index=0, id='fixed', function=NS(name='sw_status', arguments='{}'))

    class Client(FakeClient):
        async def create(self, **kwargs):
            if len(captures) == 1:
                assert bridge.calls == [('sw_status', {})], 'No call from the rejected batch may run'
                assert not any(e['type'] in {'token', 'answer', 'reasoning_delta'} for e in events)
            return await super().create(**kwargs)

    monkeypatch.setattr(module, 'AsyncOpenAI', lambda **kw: Client([
        [chunk(text, [first, second])], [chunk(calls=[fixed])], [chunk('Verified final response')],
    ], captures))

    async def emit(event):
        events.append(event)

    await CadAgent(db, settings, bridge, KnowledgeBase(db, settings)).run(session['id'], 'Inspect', emit)
    assert bridge.calls == [('sw_status', {})] * 3
    assert captures[1]['tool_choice'] == 'required'
    assert 'OUTPUT VALIDATION ERROR' in captures[1]['messages'][-1]['content']
    assert not any(m.get('tool_calls') for m in captures[1]['messages'])
    assert 'Rejected claim' not in json.dumps(captures[1]['messages'])
    assert db.messages(session['id'])[-1]['content'] == 'Verified final response'
    assert all('Rejected claim' not in e.get('content', '') for e in events
               if e['type'] != 'answer_delta')
    cleared = next(e for e in events if e['type'] == 'answer_preview_end')
    assert cleared['status'] == 'rejected' and cleared['content'] == ''


async def test_unknown_tool_is_per_call_error_and_siblings_still_run(tmp_path, monkeypatch):
    import backend.agent as module
    monkeypatch.setenv('FORMA_API_KEY', 'fixture')
    db = Database(tmp_path / 'unknown.db')
    settings = SettingsStore(tmp_path / 'settings.json')
    session = db.create_session()
    db.add_message(session['id'], 'user', 'Inspect')
    captures, events = [], []
    bridge = FakeBridge()
    first = NS(index=0, id='first', function=NS(name='sw_status', arguments='{}'))
    guessed = NS(index=1, id='bad', function=NS(name='invented', arguments='{}'))
    monkeypatch.setattr(module, 'AsyncOpenAI', lambda **kw: FakeClient(
        [[chunk('Inspecting.', [first, guessed])], [chunk('The inspection is complete.')]], captures))
    async def emit(event):
        events.append(event)
    await CadAgent(db, settings, bridge, KnowledgeBase(db, settings)).run(session['id'], 'Inspect', emit)
    # dsh-style UNKNOWN_TOOL: only the guessed call is rejected; its sibling runs.
    assert ('sw_status', {}) in bridge.calls
    assert all(name != 'invented' for name, _ in bridge.calls)
    bad = [e for e in events if e['type'] == 'tool_result' and e.get('call_id') == 'bad'][0]
    payload = json.loads(bad['content'])
    assert payload['outcome'] == 'not_executed' and payload['executed'] is False
    assert payload['category'] == 'unregistered_tool'
    assert 'invented' in payload['detail']
    good = [e for e in events if e['type'] == 'tool_result' and e.get('call_id') == 'first'][0]
    assert good['is_error'] is False
    assert db.messages(session['id'])[-1]['content'].endswith('The inspection is complete.')


async def test_disconnected_protocol_leak_is_corrected_without_registering_tools(tmp_path, monkeypatch):
    import backend.agent as module
    monkeypatch.setenv('FORMA_API_KEY', 'fixture')
    db = Database(tmp_path / 'offline.db')
    settings = SettingsStore(tmp_path / 'settings.json')
    session = db.create_session()
    db.add_message(session['id'], 'user', 'Inspect')
    bridge = FakeBridge()
    bridge.running = False
    captures = []
    monkeypatch.setattr(module, 'AsyncOpenAI', lambda **kw: FakeClient([
        [chunk('{"name":"invented","arguments":{}}')], [chunk('CAD tools are disconnected.')],
    ], captures))

    async def emit(event):
        pass

    await CadAgent(db, settings, bridge, KnowledgeBase(db, settings)).run(session['id'], 'Inspect', emit)
    # No CAD tool is registered without a connection; the local clarification card may remain.
    assert all('sw_status' not in {tool['function']['name'] for tool in request.get('tools', [])}
               for request in captures)
    assert bridge.calls == []
    assert db.messages(session['id'])[-1]['content'] == 'CAD tools are disconnected.'
