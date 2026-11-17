"""DSH-style textual call recovery plus batch/retry guard regressions."""
import json
from types import SimpleNamespace as NS

from backend.agent import CadAgent
from backend.config import SettingsStore
from backend.knowledge import KnowledgeBase
from backend.storage import Database
from test_agent import FakeBridge, FakeClient, chunk


def tool(name, schema=None, read_only=False):
    return {'name': name, 'description': name,
            'inputSchema': schema or {'type': 'object', 'properties': {}, 'additionalProperties': False},
            'annotations': {'readOnlyHint': read_only}}


def call(index, name, arguments='{}'):
    return NS(index=index, id=f'call-{index}', function=NS(name=name, arguments=arguments))


async def test_complete_dsml_in_answer_text_is_recovered_and_executed(tmp_path, monkeypatch):
    import backend.agent as module
    monkeypatch.setenv('FORMA_API_KEY', 'fixture')
    db = Database(tmp_path / 'recover.db')
    settings = SettingsStore(tmp_path / 'settings.json')
    session = db.create_session()
    db.add_message(session['id'], 'user', 'Show help for features')
    bridge = FakeBridge()
    bridge.tools = [
        tool('sw_status', read_only=True),
        tool('sw_help', {'type': 'object', 'properties': {'query': {'type': 'string'}},
                        'required': ['query'], 'additionalProperties': False}, read_only=True),
    ]
    captures, events = [], []
    dsml = ('I will inspect the help index.\n'
            '<|DSML|tool_calls>\n'
            '<|DSML|invoke name="sw_help">\n'
            '<|DSML|parameter name="query" string="true">features</|DSML|parameter>\n'
            '</|DSML|invoke>\n'
            '</|DSML|tool_calls>')
    monkeypatch.setattr(module, 'AsyncOpenAI', lambda **kw: FakeClient(
        [[chunk(dsml)], [chunk('Help received.')]], captures))
    async def emit(event):
        events.append(event)
    await CadAgent(db, settings, bridge, KnowledgeBase(db, settings)).run(
        session['id'], 'Show help for features', emit)
    assert ('sw_help', {'query': 'features'}) in bridge.calls
    assert any(e['type'] == 'status' and '已恢复 1 个' in e['message'] for e in events)
    assert any(e['type'] == 'tool_result' and e['name'] == 'sw_help' and not e['is_error']
               for e in events)
    assert all('DSML' not in (e.get('content') or '') for e in events
               if e['type'] in {'answer', 'token'})
    assert len(captures) == 2


async def test_batch_failure_does_not_skip_later_calls(tmp_path, monkeypatch):
    import backend.agent as module
    monkeypatch.setenv('FORMA_API_KEY', 'fixture')
    db = Database(tmp_path / 'batch.db')
    settings = SettingsStore(tmp_path / 'settings.json')
    session = db.create_session()
    db.add_message(session['id'], 'user', 'Inspect and help')
    bridge = FakeBridge()
    bridge.tools = [
        tool('sw_status', read_only=True),
        tool('model_info', read_only=True),
        tool('sw_help', {'type': 'object', 'properties': {'query': {'type': 'string'}},
                        'required': ['query'], 'additionalProperties': False}, read_only=True),
        tool('doc_new', {'type': 'object', 'properties': {'name': {'type': 'string'}},
                         'additionalProperties': False}),
    ]
    calls = [call(0, 'model_info'), call(1, 'sw_help', '{"query":"features"}'), call(2, 'doc_new', '{"name":"x"}')]
    captures, events = [], []
    async def result(name, arguments):
        bridge.calls.append((name, arguments))
        if name == 'model_info':
            return {'content': '{"error":"NOT_CONNECTED"}', 'is_error': True}
        if name == 'sw_help':
            return {'content': '{"help":"ok"}', 'is_error': False}
        return {'content': '{"created":true}', 'is_error': False}
    bridge.call = result
    monkeypatch.setattr(module, 'AsyncOpenAI', lambda **kw: FakeClient(
        [[chunk(calls=calls)], [chunk('Done.')]], captures))
    async def emit(event):
        events.append(event)
    await CadAgent(db, settings, bridge, KnowledgeBase(db, settings)).run(session['id'], 'Inspect and help', emit)
    # A failed call is a result, not a batch abort (dsh batch semantics).
    assert [name for name, _ in bridge.calls].count('model_info') == 1
    assert [name for name, _ in bridge.calls].count('sw_help') == 1
    assert 'doc_new' in [name for name, _ in bridge.calls]
    doc = [e for e in events if e['type'] == 'tool_result' and e['name'] == 'doc_new'][0]
    assert doc['is_error'] is False
    assert not any(e['type'] == 'tool_result' and e.get('category') == 'skipped_after_failure'
                   for e in events)
    assert any(e['type'] == 'status' and '成功 2' in e['message'] and '失败 1' in e['message']
               for e in events)


async def test_textual_json_array_is_recovered_and_executed(tmp_path, monkeypatch):
    import backend.agent as module
    monkeypatch.setenv('FORMA_API_KEY', 'fixture')
    db = Database(tmp_path / 'json-array.db')
    settings = SettingsStore(tmp_path / 'settings.json')
    session = db.create_session()
    db.add_message(session['id'], 'user', 'Create a document')
    bridge = FakeBridge()
    bridge.tools = [
        tool('sw_status', read_only=True),
        tool('doc_new', {'type': 'object', 'properties': {'name': {'type': 'string'}},
                         'required': ['name'], 'additionalProperties': False}),
    ]
    text = ('Alright.\n'
            '[{"name": "doc_new", "arguments": {"name": "recovered"}}]')
    captures, events = [], []
    monkeypatch.setattr(module, 'AsyncOpenAI', lambda **kw: FakeClient(
        [[chunk(text)], [chunk('Done.')]], captures))
    async def emit(event):
        events.append(event)
    await CadAgent(db, settings, bridge, KnowledgeBase(db, settings)).run(session['id'], 'Create', emit)
    assert ('doc_new', {'name': 'recovered'}) in bridge.calls
    assert any(e['type'] == 'status' and '已恢复 1 个' in e['message'] for e in events)


async def test_singular_tool_call_tag_and_bare_json_are_recovered(tmp_path, monkeypatch):
    import backend.agent as module
    monkeypatch.setenv('FORMA_API_KEY', 'fixture')
    db = Database(tmp_path / 'single.db')
    settings = SettingsStore(tmp_path / 'settings.json')
    session = db.create_session()
    db.add_message(session['id'], 'user', 'Show help')
    bridge = FakeBridge()
    bridge.tools = [
        tool('sw_status', read_only=True),
        tool('sw_help', {'type': 'object', 'properties': {'query': {'type': 'string'}},
                        'required': ['query'], 'additionalProperties': False}, read_only=True),
    ]
    text = '<tool_call>{"name":"sw_help","arguments":{"query":"features"}}</tool_call>'
    captures, events = [], []
    monkeypatch.setattr(module, 'AsyncOpenAI', lambda **kw: FakeClient(
        [[chunk(text)], [chunk('Done.')]], captures))
    async def emit(event):
        events.append(event)
    await CadAgent(db, settings, bridge, KnowledgeBase(db, settings)).run(session['id'], 'Help', emit)
    assert ('sw_help', {'query': 'features'}) in bridge.calls


async def test_dsml_in_reasoning_channel_is_recovered_when_content_has_none(tmp_path, monkeypatch):
    import backend.agent as module
    monkeypatch.setenv('FORMA_API_KEY', 'fixture')
    db = Database(tmp_path / 'reasoning.db')
    settings = SettingsStore(tmp_path / 'settings.json')
    session = db.create_session()
    db.add_message(session['id'], 'user', 'Show help')
    bridge = FakeBridge()
    bridge.tools = [
        tool('sw_status', read_only=True),
        tool('sw_help', {'type': 'object', 'properties': {'query': {'type': 'string'}},
                        'required': ['query'], 'additionalProperties': False}, read_only=True),
    ]
    dsml = ('<|DSML|tool_calls>\n'
            '<|DSML|invoke name="sw_help">\n'
            '<|DSML|parameter name="query" string="true">features</|DSML|parameter>\n'
            '</|DSML|invoke>\n'
            '</|DSML|tool_calls>')
    reasoning_chunk = NS(choices=[NS(delta=NS(content=None, tool_calls=[], reasoning_content=dsml),
                                     finish_reason=None)])
    captures, events = [], []
    monkeypatch.setattr(module, 'AsyncOpenAI', lambda **kw: FakeClient(
        [[reasoning_chunk], [chunk('Done.')]], captures))
    async def emit(event):
        events.append(event)
    await CadAgent(db, settings, bridge, KnowledgeBase(db, settings)).run(session['id'], 'Help', emit)
    assert ('sw_help', {'query': 'features'}) in bridge.calls
    assert any(e['type'] == 'status' and '已恢复 1 个' in e['message'] for e in events)


async def test_python_style_tool_call_is_recovered(tmp_path, monkeypatch):
    import backend.agent as module
    monkeypatch.setenv('FORMA_API_KEY', 'fixture')
    db = Database(tmp_path / 'fn.db')
    settings = SettingsStore(tmp_path / 'settings.json')
    session = db.create_session()
    db.add_message(session['id'], 'user', 'Show help')
    bridge = FakeBridge()
    bridge.tools = [
        tool('sw_status', read_only=True),
        tool('sw_help', {'type': 'object', 'properties': {'query': {'type': 'string'}},
                        'required': ['query'], 'additionalProperties': False}, read_only=True),
    ]
    captures, events = [], []
    monkeypatch.setattr(module, 'AsyncOpenAI', lambda **kw: FakeClient(
        [[chunk('sw_help(query="features")')], [chunk('Done.')]], captures))
    async def emit(event):
        events.append(event)
    await CadAgent(db, settings, bridge, KnowledgeBase(db, settings)).run(session['id'], 'Help', emit)
    assert ('sw_help', {'query': 'features'}) in bridge.calls


async def test_fullwidth_spaced_dsml_calls_envelope_is_recovered(tmp_path, monkeypatch):
    import backend.agent as module
    monkeypatch.setenv('FORMA_API_KEY', 'fixture')
    db = Database(tmp_path / 'fullwidth.db')
    settings = SettingsStore(tmp_path / 'settings.json')
    session = db.create_session()
    db.add_message(session['id'], 'user', 'Save the document')
    bridge = FakeBridge()
    bridge.tools = [
        tool('sw_status', read_only=True),
        tool('doc_new', {'type': 'object', 'properties': {
            'name': {'type': 'string'}},
            'required': ['name'], 'additionalProperties': True}),
    ]
    text = ('第 48 轮：\n\n'
            '<｜｜DSML｜｜ calls>\n'
            '<｜｜DSML｜｜ invoke name="doc_new">\n'
            '<｜｜DSML｜｜ parameter name="name" string="true">forma_test48</｜｜DSML｜｜ parameter>\n'
            '</｜｜DSML｜｜ invoke>\n'
            '</｜｜DSML｜｜ calls>')
    captures, events = [], []
    monkeypatch.setattr(module, 'AsyncOpenAI', lambda **kw: FakeClient(
        [[chunk(text)], [chunk('Saved.')]], captures))
    async def emit(event):
        events.append(event)
    await CadAgent(db, settings, bridge, KnowledgeBase(db, settings)).run(session['id'], 'Save the document', emit)
    assert any(name == 'doc_new' for name, _ in bridge.calls)
    assert any(e['type'] == 'status' and '已恢复 1 个' in e['message'] for e in events)
    assert all('DSML' not in (e.get('content') or '') for e in events
               if e['type'] in {'answer', 'token'})


async def test_failed_help_does_not_skip_later_mutations(tmp_path, monkeypatch):
    import backend.agent as module
    monkeypatch.setenv('FORMA_API_KEY', 'fixture')
    db = Database(tmp_path / 'help.db')
    settings = SettingsStore(tmp_path / 'settings.json')
    session = db.create_session()
    db.add_message(session['id'], 'user', 'Create a document')
    bridge = FakeBridge()
    bridge.tools = [
        tool('sw_status', read_only=True),
        tool('sw_help', {'type': 'object', 'properties': {'query': {'type': 'string'}},
                        'required': ['query'], 'additionalProperties': False}, read_only=True),
        tool('doc_new', {'type': 'object', 'properties': {'name': {'type': 'string'}},
                         'additionalProperties': False}),
    ]
    calls = [call(0, 'sw_help', '{"query":"features"}'), call(1, 'doc_new', '{"name":"x"}')]
    captures, events = [], []
    async def result(name, arguments):
        bridge.calls.append((name, arguments))
        if name == 'sw_help':
            return {'content': 'help index unavailable', 'is_error': True}
        return {'content': '{"created":true}', 'is_error': False}
    bridge.call = result
    monkeypatch.setattr(module, 'AsyncOpenAI', lambda **kw: FakeClient(
        [[chunk(calls=calls)], [chunk('Done.')]], captures))
    async def emit(event):
        events.append(event)
    await CadAgent(db, settings, bridge, KnowledgeBase(db, settings)).run(session['id'], 'Create a document', emit)
    assert 'doc_new' in [name for name, _ in bridge.calls]


async def test_identical_failure_is_allowed_after_live_state_changes(tmp_path, monkeypatch):
    import backend.agent as module
    monkeypatch.setenv('FORMA_API_KEY', 'fixture')
    db = Database(tmp_path / 'state.db')
    settings = SettingsStore(tmp_path / 'settings.json')
    session = db.create_session()
    db.add_message(session['id'], 'user', 'Inspect model')
    bridge = FakeBridge()
    bridge.sw_status = {'attached': False}
    bridge.tools = [tool('sw_status', read_only=True), tool('model_info', read_only=True)]
    bridge.flip_on_status = True
    async def result(name, arguments):
        bridge.calls.append((name, arguments))
        if name == 'sw_status':
            if bridge.flip_on_status:
                bridge.sw_status = {'attached': True, 'solidWorksPid': 42}
                bridge.flip_on_status = False
            return {'content': json.dumps(bridge.sw_status), 'is_error': False}
        if name == 'model_info':
            if not bridge.sw_status.get('attached'):
                return {'content': '{"error":"NOT_CONNECTED"}', 'is_error': True}
            return {'content': '{"title":"plate"}', 'is_error': False}
        return {'content': '{}', 'is_error': False}
    bridge.call = result
    captures, events = [], []
    monkeypatch.setattr(module, 'AsyncOpenAI', lambda **kw: FakeClient(
        [[chunk(calls=[call(0, 'model_info')])],
         [chunk(calls=[call(1, 'model_info')])],
         [chunk('Model inspected.')]], captures))
    async def emit(event):
        events.append(event)
    await CadAgent(db, settings, bridge, KnowledgeBase(db, settings)).run(session['id'], 'Inspect model', emit)
    model_calls = [name for name, _ in bridge.calls if name == 'model_info']
    assert model_calls == ['model_info', 'model_info']
    assert not any(e['type'] == 'tool_result' and e.get('name') == 'model_info'
                   and json.loads(e['content']).get('category') == 'identical_failed_call'
                   for e in events)


async def test_identical_failure_is_advisory_and_runs_again(tmp_path, monkeypatch):
    import backend.agent as module
    monkeypatch.setenv('FORMA_API_KEY', 'fixture')
    db = Database(tmp_path / 'same.db')
    settings = SettingsStore(tmp_path / 'settings.json')
    session = db.create_session()
    db.add_message(session['id'], 'user', 'Inspect model')
    bridge = FakeBridge()
    bridge.sw_status = {'attached': False}
    bridge.tools = [tool('sw_status', read_only=True), tool('model_info', read_only=True)]
    async def result(name, arguments):
        bridge.calls.append((name, arguments))
        if name == 'model_info':
            return {'content': '{"error":"NOT_CONNECTED"}', 'is_error': True}
        return {'content': json.dumps(bridge.sw_status), 'is_error': False}
    bridge.call = result
    captures, events = [], []
    monkeypatch.setattr(module, 'AsyncOpenAI', lambda **kw: FakeClient(
        [[chunk(calls=[call(0, 'model_info')])],
         [chunk(calls=[call(1, 'model_info')])],
         [chunk('Still disconnected.')]], captures))
    async def emit(event):
        events.append(event)
    await CadAgent(db, settings, bridge, KnowledgeBase(db, settings)).run(session['id'], 'Inspect model', emit)
    # dsh-style repeat guard: advise, never turn a retry into a fake failure.
    assert [name for name, _ in bridge.calls].count('model_info') == 2
    results = [json.loads(e['content']) for e in events
               if e['type'] == 'tool_result' and e['name'] == 'model_info']
    assert results[-1]['error'] == 'NOT_CONNECTED'
    assert results[-1]['repeat_of_failed_call'] is True
    assert results[-1]['state_unchanged'] is True
    assert results[-1]['previous_error'].find('NOT_CONNECTED') >= 0
    assert 'repeat_notice' in results[-1]
    assert not any(item.get('category') == 'identical_failed_call' for item in results)


async def test_textual_call_after_old_round_ceiling_executes_and_continues(tmp_path, monkeypatch):
    import backend.agent as module
    monkeypatch.setenv('FORMA_API_KEY', 'fixture')
    db = Database(tmp_path / 'budget.db')
    settings = SettingsStore(tmp_path / 'settings.json')
    session = db.create_session()
    db.add_message(session['id'], 'user', 'Try one more edit')
    bridge = FakeBridge()
    bridge.tools = [
        tool('sw_status', read_only=True),
        tool('doc_new', {'type': 'object', 'properties': {'name': {'type': 'string'}},
                         'required': ['name'], 'additionalProperties': False}),
    ]
    text = ('<｜｜DSML｜｜ calls>\n'
            '<｜｜DSML｜｜ invoke name="doc_new">\n'
            '<｜｜DSML｜｜ parameter name="name" string="true">budget</｜｜DSML｜｜ parameter>\n'
            '</｜｜DSML｜｜ invoke>\n'
            '</｜｜DSML｜｜ calls>')
    captures, events = [], []
    prelude = [[chunk(calls=[NS(index=0, id=f'prior-{i}', function=NS(name='sw_status', arguments='{}'))])]
               for i in range(18)]
    monkeypatch.setattr(module, 'AsyncOpenAI', lambda **kw: FakeClient(
        prelude + [[chunk(text)], [chunk('Document created.')]], captures))
    async def emit(event):
        events.append(event)
    await CadAgent(db, settings, bridge, KnowledgeBase(db, settings)).run(session['id'], 'Try one more edit', emit)
    assert [name for name, _ in bridge.calls].count('doc_new') == 1
    assert len(captures) == 20
    assert any(t['function']['name'] == 'doc_new' for t in captures[18]['tools'])
    assert any(e['type'] == 'tool_result' and e.get('name') == 'doc_new' and e.get('round') == 19 for e in events)
    assert not any(e['type'] == 'status' and '额度已用尽' in e['message'] for e in events)
    assert db.messages(session['id'])[-1]['content'] == 'Document created.'
    assert all('DSML' not in (e.get('content') or '') for e in events
               if e['type'] in {'answer', 'token'})
