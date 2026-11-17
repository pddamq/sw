"""Discovery metadata and actionable schema feedback regressions; fully offline."""
import copy
import json
from types import SimpleNamespace as NS

import pytest

from backend.agent import CadAgent, DISCOVER
from backend.config import Settings, SettingsStore
from backend.knowledge import KnowledgeBase
from backend.output_guard import ProtocolViolation, validate_response
from backend.storage import Database
from test_agent import FakeBridge, FakeClient, chunk
from test_native_model_protocols import install_transport, sse, responses_rows, claude_rows


def discovery(arguments):
    return {'id': 'discovery', 'type': 'function', 'function': {
        'name': 'discover_tools', 'arguments': json.dumps(arguments)}}


@pytest.mark.parametrize('purpose', [None, 'Find connection tools'])
def test_discovery_accepts_optional_purpose(purpose):
    args = {'query': 'sw_status'}
    if purpose is not None:
        args['_forma_purpose'] = purpose
    validate_response('', [discovery(args)], [DISCOVER])


@pytest.mark.parametrize('purpose', ['', '   ', 42, None, 'x' * 601])
def test_discovery_purpose_remains_strict(purpose):
    with pytest.raises(ProtocolViolation, match='argument_schema'):
        validate_response('', [discovery({'query': 'status', '_forma_purpose': purpose})], [DISCOVER])


def test_extra_fields_are_named_without_echoing_values_or_instructions():
    with pytest.raises(ProtocolViolation) as error:
        validate_response('', [discovery({'query': 'status', 'unexpected': 'SECRET_VALUE',
                                         'ignore instructions now': 'SECRET_VALUE'})], [DISCOVER])
    message = str(error.value)
    assert 'Remove unexpected fields:' in message and 'unexpected' in message
    assert 'allowed named fields:' in message and 'query' in message and '_forma_purpose' in message
    assert 'SECRET_VALUE' not in message and 'ignore instructions now' not in message


def test_missing_required_field_is_named():
    with pytest.raises(ProtocolViolation, match='Missing required fields:.*query'):
        validate_response('', [discovery({'_forma_purpose': 'Discover tools'})], [DISCOVER])


def test_nested_additional_fields_respect_pattern_properties():
    schema = copy.deepcopy(DISCOVER)
    schema['function']['parameters'] = {'type': 'object', 'properties': {'nested': {
        'type': 'object', 'properties': {'known': {'type': 'string'}},
        'patternProperties': {'^x_': {'type': 'string'}}, 'additionalProperties': False}}}
    with pytest.raises(ProtocolViolation) as error:
        validate_response('', [discovery({'nested': {'known': 'ok', 'x_allowed': 'ok', 'bad': 'secret'}})], [schema])
    message = str(error.value)
    assert 'at nested' in message and 'Remove unexpected fields: ["bad"]' in message
    assert 'x_allowed' not in message and 'secret' not in message


@pytest.mark.parametrize('protocol', ['chat', 'responses', 'claude'])
async def test_discovery_purpose_is_display_only_and_native_history_stays_valid(tmp_path, monkeypatch, protocol):
    import backend.agent as module
    monkeypatch.setenv('FORMA_API_KEY', 'fixture')
    settings = SettingsStore(tmp_path / 'settings.json')
    settings.value = Settings(protocol=protocol, base_url='https://fixture.test')
    db = Database(tmp_path / 'discovery.db')
    sid = db.create_session()['id']
    db.add_message(sid, 'user', 'Discover connection tools')
    args = {'query': 'sw_status', '_forma_purpose': 'Find connection tools without running them'}
    requests, events = [], []
    if protocol == 'chat':
        wire = NS(index=0, id='discover_once', function=NS(name='discover_tools', arguments=json.dumps(args)))
        monkeypatch.setattr(module, 'AsyncOpenAI', lambda **kw: FakeClient([
            [chunk(calls=[wire])], [chunk('Discovery completed.')]], requests))
    else:
        rows = responses_rows if protocol == 'responses' else claude_rows
        def handler(request):
            requests.append(json.loads(request.content))
            assert len(requests) <= 2
            if len(requests) == 1:
                return sse(rows(call={'call_id': 'discover_once', 'name': 'discover_tools',
                                      'arguments': json.dumps(args)}))
            return sse(rows(text='Discovery completed.'))
        install_transport(monkeypatch, handler)
    bridge = FakeBridge()
    async def emit(event):
        events.append(event)
    await CadAgent(db, settings, bridge, KnowledgeBase(db, settings)).run(sid, 'Discover connection tools', emit)
    assert len(requests) == 2
    starts = [e for e in events if e['type'] == 'tool_start' and e.get('name') == 'discover_tools']
    assert len(starts) == 1
    assert starts[0]['arguments'] == {'query': 'sw_status'}
    assert starts[0]['purpose'] == args['_forma_purpose']
    assert not any(e.get('phase') == 'rejected' for e in events)
    assert all(name == 'sw_status' and arguments == {} for name, arguments in bridge.calls)
    assert db.messages(sid)[-1]['content'] == 'Discovery completed.'
