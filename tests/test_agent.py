from types import SimpleNamespace as NS

from backend.agent import CadAgent
from backend.config import SettingsStore
from backend.knowledge import KnowledgeBase
from backend.storage import Database


def chunk(content=None, calls=None):
    return NS(choices=[NS(delta=NS(content=content, tool_calls=calls or []), finish_reason=None)])


class FakeStream:
    def __init__(self, chunks):
        self.chunks = chunks

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False

    def __aiter__(self):
        return self.generate()

    async def generate(self):
        for item in self.chunks:
            yield item
        if not any(choice.finish_reason for item in self.chunks for choice in item.choices):
            yield NS(choices=[NS(delta=NS(content=None, tool_calls=[]), finish_reason='stop')])


class FakeClient:
    def __init__(self, responses, captures):
        self.responses = iter(responses)
        self.captures = captures
        self.chat = NS(completions=self)

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False

    async def create(self, **kwargs):
        self.captures.append(kwargs)
        result = next(self.responses)
        if kwargs.get('stream'):
            return FakeStream(result)
        return NS(choices=[NS(message=NS(content=result))])


class FakeBridge:
    running = True
    sw_status = {'attached': False, 'note': 'fixture, not real CAD'}
    tools = [{'name': 'sw_status', 'description': 'Read connection status',
              'inputSchema': {'type': 'object', 'properties': {}, 'additionalProperties': False}}]

    def __init__(self):
        self.calls = []

    async def call(self, name, arguments):
        self.calls.append((name, arguments))
        return {'content': '{"attached":false,"note":"fixture: no SW"}', 'is_error': False}


async def test_actual_langgraph_tool_cycle(tmp_path, monkeypatch):
    import backend.agent as module
    db = Database(tmp_path / 'graph.db')
    config = SettingsStore(tmp_path / 'settings.json')
    monkeypatch.setenv('FORMA_API_KEY', 'test-key')
    captures = []
    call = NS(index=0, id='call_1', function=NS(name='sw_status', arguments='{}'))
    responses = [[chunk('检查连接。', [call])], [chunk('测试服务报告 SW 未连接。')]]
    monkeypatch.setattr(module, 'AsyncOpenAI', lambda **kw: FakeClient(responses, captures))
    bridge = FakeBridge()
    agent = CadAgent(db, config, bridge, KnowledgeBase(db, config))
    session = db.create_session()
    db.add_message(session['id'], 'user', '检查 SW 状态')
    events = []
    async def emit(event):
        events.append(event)
    await agent.run(session['id'], '检查 SW 状态', emit)
    assert bridge.calls == [('sw_status', {}), ('sw_status', {}), ('sw_status', {})]
    assert any(e['type'] == 'tool_result' for e in events)
    assert '未连接' in db.messages(session['id'])[-1]['content']
    assert captures[1]['messages'][-1]['role'] == 'tool'
    answers = [e for e in events if e['type'] == 'answer']
    assert [(e['round'], e['content']) for e in answers] == [(1, '检查连接。'), (2, '测试服务报告 SW 未连接。')]
    assert all(e['round'] in {1, 2} for e in events if e['type'] in {'tool_start', 'tool_result', 'token'})


async def test_schema_validation_prevents_bad_calls(tmp_path, monkeypatch):
    import backend.agent as module
    db = Database(tmp_path / 'graph.db')
    config = SettingsStore(tmp_path / 'settings.json')
    monkeypatch.setenv('FORMA_API_KEY', 'test-key')
    call = NS(index=0, id='call_bad', function=NS(name='sw_status', arguments='{"bogus":true}'))
    captures = []
    corrected = NS(index=0, id='call_fixed', function=NS(name='sw_status', arguments='{}'))
    responses = [[chunk('Discard this invalid response.', calls=[call])],
                 [chunk(calls=[corrected])], [chunk('参数已纠正，状态已读取。')]]
    monkeypatch.setattr(module, 'AsyncOpenAI', lambda **kw: FakeClient(responses, captures))
    bridge = FakeBridge()
    agent = CadAgent(db, config, bridge, KnowledgeBase(db, config))
    session = db.create_session()
    db.add_message(session['id'], 'user', '检查')
    events = []
    async def emit(event):
        events.append(event)
    await agent.run(session['id'], '检查', emit)
    assert bridge.calls == [('sw_status', {}), ('sw_status', {}), ('sw_status', {})]
    assert any(e['type'] == 'status' and '协议校验' in e['message'] for e in events)
    assert not any(e['type'] == 'tool_result' and e['is_error'] for e in events)
    assert 'Discard this' not in db.messages(session['id'])[-1]['content']
    assert captures[1]['tool_choice'] == 'required'


async def test_many_short_messages_do_not_trigger_compression(tmp_path, monkeypatch):
    import backend.agent as module
    db = Database(tmp_path / 'graph.db')
    config = SettingsStore(tmp_path / 'settings.json')
    monkeypatch.setenv('FORMA_API_KEY', 'test-key')
    captures = []
    responses = [[chunk('已读取历史。')]]
    monkeypatch.setattr(module, 'AsyncOpenAI', lambda **kw: FakeClient(responses, captures))
    bridge = FakeBridge()
    bridge.running = False
    session = db.create_session()
    for i in range(26):
        db.add_message(session['id'], 'user' if i % 2 == 0 else 'assistant', f'fixture message {i}')
    async def emit(event):
        pass
    await CadAgent(db, config, bridge, KnowledgeBase(db, config)).run(session['id'], '继续', emit)
    assert db.session(session['id'])['summary_through'] == 0
    assert len(db.messages(session['id'])) == 27
    assert len(captures) == 1
    assert len(captures[0]['messages']) == 27
