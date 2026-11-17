import json

import httpx
from openai import AsyncOpenAI

from backend.agent import CadAgent
from backend.config import SettingsStore
from backend.knowledge import KnowledgeBase
from backend.storage import Database


async def test_openai_compatible_sse_through_real_sdk(tmp_path, monkeypatch):
    import backend.agent as module
    db = Database(tmp_path / 'http-model.db')
    config = SettingsStore(tmp_path / 'settings.json')
    config.update({'base_url': 'http://model-fixture.local/v1', 'model': 'fixture'})
    monkeypatch.setenv('FORMA_API_KEY', 'fixture-key')
    captures = []
    def handler(request):
        captures.append(json.loads(request.content))
        data = {'id': 'fixture', 'object': 'chat.completion.chunk', 'created': 0, 'model': 'fixture',
                'choices': [{'index': 0, 'delta': {'content': '这是一条协议测试回复，未操作 CAD。'}, 'finish_reason': 'stop'}]}
        return httpx.Response(200, headers={'content-type': 'text/event-stream'},
            content=('data: ' + json.dumps(data, ensure_ascii=False) + '\n\ndata: [DONE]\n\n').encode())
    transport = httpx.MockTransport(handler)
    monkeypatch.setattr(module, 'AsyncOpenAI', lambda **kwargs: AsyncOpenAI(
        **kwargs, http_client=httpx.AsyncClient(transport=transport)))
    class OfflineBridge:
        running = False
        tools = []
        sw_status = {}
    session = db.create_session()
    db.add_message(session['id'], 'user', '检查协议')
    events = []
    async def emit(event):
        events.append(event)
    await CadAgent(db, config, OfflineBridge(), KnowledgeBase(db, config)).run(session['id'], '检查协议', emit)
    assert '协议测试' in db.messages(session['id'])[-1]['content']
    assert captures[0]['stream'] is True
    assert 'fixture-key' not in json.dumps(captures)
