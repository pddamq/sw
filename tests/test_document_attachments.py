import httpx
import pytest

from backend.app import create_app
from backend.config import SettingsStore
from backend.knowledge import KnowledgeBase
from backend.media import MediaStore
from backend.storage import Database
from test_agent import FakeBridge, FakeClient, chunk


async def test_document_lifecycle_without_vision_and_session_isolation(tmp_path, monkeypatch):
    monkeypatch.setenv('FORMA_API_KEY', 'fixture')
    db = Database(tmp_path / 'files.db')
    settings = SettingsStore(tmp_path / 'settings.json')
    app = create_app(db, settings, FakeBridge(), token='fixture')
    async def run(session_id, query, emit):
        pass
    monkeypatch.setattr(app.state.agent, 'run', run)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url='http://testserver', headers={'Authorization': 'Bearer fixture'}) as api:
        first = db.create_session()['id']
        second = db.create_session()['id']
        path = f'/api/sessions/{first}/attachments'
        response = await api.post(path, files={'file': ('../dimensions.txt', b'Diameter = 24 mm')})
        assert response.status_code == 200
        item = response.json()
        assert item['kind'] == 'document' and item['name'] == 'dimensions.txt'
        assert 'content' not in item
        assert (await api.get(path)).json()[0]['id'] == item['id']
        assert (await api.post('/api/chat', json={'session_id': second, 'message': 'Read', 'attachment_ids': [item['id']]})).status_code == 400
        assert not db.messages(second)
        response = await api.post('/api/chat', json={'session_id': first, 'message': 'Read', 'attachment_ids': [item['id']]})
        assert response.status_code == 200
        assert not settings.value.vision_enabled
        assert db.messages(first)[0]['attachments'][0]['kind'] == 'document'
        assert (await api.get(path)).json() == []
        assert (await api.delete('/api/media/' + item['id'])).status_code == 400
        assert (await api.post('/api/chat', json={'session_id': first, 'message': 'Read', 'attachment_ids': [item['id']]})).status_code == 400
        await api.delete('/api/sessions/' + first)
        assert (await api.get('/api/media/' + item['id'])).status_code == 404


async def test_document_text_reaches_model_without_image_payload(tmp_path, monkeypatch):
    from backend.agent import CadAgent
    import backend.agent as module
    monkeypatch.setenv('FORMA_API_KEY', 'fixture')
    db = Database(tmp_path / 'files.db')
    settings = SettingsStore(tmp_path / 'settings.json')
    session = db.create_session()['id']
    media = MediaStore(db)
    item = await media.add_document(session, 'dimensions.csv', b'width,height\n24,48')
    db.create_run(session, 'Read attached dimensions', [item['id']])
    captures = []
    monkeypatch.setattr(module, 'AsyncOpenAI', lambda **kw: FakeClient([[chunk('Read successfully')]], captures))
    async def emit(event):
        pass
    await CadAgent(db, settings, FakeBridge(), KnowledgeBase(db, settings)).run(session, 'Read attached dimensions', emit)
    content = next(m['content'] for m in captures[0]['messages'] if m['role'] == 'user')
    assert isinstance(content, str)
    assert 'dimensions.csv' in content and '24,48' in content
    assert 'untrusted reference data' in content
    assert 'image_url' not in content
    assert db.knowledge_page()['total'] == 0


async def test_document_validation_limits_and_pending_removal(tmp_path):
    from test_media import picture
    db = Database(tmp_path / 'files.db')
    media = MediaStore(db)
    session = db.create_session()['id']
    for name, data in [('bad.exe', b'payload'), ('empty.txt', b''), ('binary.txt', b'abc\x00abc'), ('large.txt', b'a' * 24001), ('bad.pdf', b'bad pdf')]:
        with pytest.raises(ValueError):
            await media.add_document(session, name, data)
    doc = await media.add_document(session, 'test.html', b'<p>Dimensions</p><script>hidden()</script>')
    assert media.get(doc['id'])['content'].decode() == 'Dimensions'
    for i in range(3):
        await media.add(session, f'{i}.png', picture())
    with pytest.raises(ValueError):
        await media.add_document(session, 'fifth.txt', b'five')
    with pytest.raises(ValueError):
        await media.add(session, 'fifth.png', picture())
    assert media.discard(doc['id'])
    assert len(media.pending(session)) == 3
    assert media.document_context([], session) == ''
    with pytest.raises(ValueError):
        media.document_context([doc], db.create_session()['id'])
