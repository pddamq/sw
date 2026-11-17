import base64
import pytest
from types import SimpleNamespace as NS

from backend.agent import CadAgent
from backend.config import SettingsStore
from backend.knowledge import KnowledgeBase
from backend.media import MediaStore
from backend.storage import Database
from test_agent import FakeBridge, FakeClient, chunk
from test_media import picture


async def test_reference_pixels_only_sent_when_vision_enabled(tmp_path, monkeypatch):
    import backend.agent as module
    monkeypatch.setenv('FORMA_API_KEY', 'fixture')
    db = Database(tmp_path / 'vision.db')
    config = SettingsStore(tmp_path / 'settings.json')
    session = db.create_session()
    media = MediaStore(db)
    image = await media.add(session['id'], 'reference', picture())
    db.create_run(session['id'], 'describe reference', [image['id']])
    bridge = FakeBridge(); bridge.running = False
    async def emit(event):
        pass
    captures = []
    monkeypatch.setattr(module, 'AsyncOpenAI', lambda **kw: FakeClient([[chunk('fixture')]], captures))
    await CadAgent(db, config, bridge, KnowledgeBase(db, config)).run(session['id'], 'describe reference', emit)
    assert isinstance(captures[0]['messages'][-1]['content'], str)
    config.update({'vision_enabled': True})
    captures.clear()
    monkeypatch.setattr(module, 'AsyncOpenAI', lambda **kw: FakeClient([[chunk('fixture')]], captures))
    await CadAgent(db, config, bridge, KnowledgeBase(db, config)).run(session['id'], 'describe reference', emit)
    user_message = next(m for m in captures[0]['messages'] if m['role'] == 'user')
    assert user_message['content'][1]['type'] == 'image_url'
    assert user_message['content'][1]['image_url']['url'].startswith('data:image/png;base64,')


@pytest.mark.parametrize('vision', [True, False])
async def test_tool_image_is_displayed_and_sent_as_visual_feedback(tmp_path, monkeypatch, vision):
    import backend.agent as module
    monkeypatch.setenv('FORMA_API_KEY', 'fixture')
    db = Database(tmp_path / 'screens.db')
    config = SettingsStore(tmp_path / 'settings.json')
    config.update({'vision_enabled': vision})
    session = db.create_session()
    db.create_run(session['id'], 'fixture_image')
    class ImageBridge(FakeBridge):
        tools = [{'name': 'fixture_image', 'description': 'synthetic image protocol fixture, not CAD',
                  'inputSchema': {'type': 'object', 'properties': {}}}]
        async def call(self, name, arguments):
            return {'content': 'synthetic fixture image, no CAD', 'is_error': False,
                    'images': [{'data': base64.b64encode(picture()).decode(), 'mime_type': 'image/png'}]}
    call = NS(index=0, id='fixture_image_call', function=NS(name='fixture_image', arguments='{}'))
    captures = []
    monkeypatch.setattr(module, 'AsyncOpenAI', lambda **kw: FakeClient([[chunk(calls=[call])], [chunk('fixture done')]], captures))
    events = []
    async def emit(event):
        events.append(event)
    await CadAgent(db, config, ImageBridge(), KnowledgeBase(db, config)).run(session['id'], 'fixture_image', emit)
    result = next(e for e in events if e['type'] == 'tool_result')
    assert result['images'][0]['kind'] == 'tool'
    assert 'data' not in result['images'][0]
    if vision:
        assert captures[1]['messages'][-1]['role'] == 'user'
        assert any(p['type'] == 'image_url' for p in captures[1]['messages'][-1]['content'])
    else:
        assert captures[1]['messages'][-1]['role'] == 'tool'
        assert 'Visual input is disabled' in captures[1]['messages'][-1]['content']
