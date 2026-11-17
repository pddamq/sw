import base64
import io

import httpx
import pytest
from PIL import Image

from backend.app import create_app
from backend.config import SettingsStore
from backend.media import MAX_IMAGE_BYTES, MediaStore, normalize_image
from backend.storage import Database


def picture(size=(40, 30), fmt='PNG'):
    output = io.BytesIO()
    Image.new('RGB', size, '#334455').save(output, format=fmt)
    return output.getvalue()


@pytest.fixture
def setup(tmp_path, monkeypatch):
    monkeypatch.setenv('FORMA_API_KEY', 'fixture')
    db = Database(tmp_path / 'media.db')
    settings = SettingsStore(tmp_path / 'settings.json')
    app = create_app(db, settings, token='fixture')
    return db, settings, app


def api(app, auth=True):
    return httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url='http://testserver',
                            headers={'Authorization': 'Bearer fixture'} if auth else {})


def test_normalize_reencodes_and_bounds_images():
    raw, width, height = normalize_image(picture((2000, 1000), 'JPEG'))
    assert raw.startswith(b'\x89PNG') and (width, height) == (1600, 800)
    with pytest.raises(ValueError):
        normalize_image(b'<svg onload="alert(1)"/>')
    with pytest.raises(ValueError):
        normalize_image(b'X' * (MAX_IMAGE_BYTES + 1))
    with pytest.raises(ValueError):
        normalize_image(picture(fmt='GIF'))


async def test_reference_lifecycle_and_image_auth(setup, monkeypatch):
    db, settings, app = setup
    async def fake(session_id, query, emit):
        await emit({'type': 'token', 'content': 'fixture no CAD'})
    monkeypatch.setattr(app.state.agent, 'run', fake)
    async with api(app) as client:
        session = (await client.post('/api/sessions', json={})).json()
        image = (await client.post(f"/api/sessions/{session['id']}/attachments", files={'file': ('../参考.jpg', picture())})).json()
        assert image['name'] == '参考.png' and image['kind'] == 'reference'
        response = await client.get('/api/media/' + image['id'])
        assert response.status_code == 200 and response.headers['content-type'] == 'image/png'
        assert response.content.startswith(b'\x89PNG')
        async with api(app, auth=False) as unauthenticated:
            assert (await unauthenticated.get('/api/media/' + image['id'])).status_code == 401
        payload = {'session_id': session['id'], 'message': 'fixture', 'attachment_ids': [image['id']]}
        assert (await client.post('/api/chat', json=payload)).status_code == 400
        assert len(db.messages(session['id'])) == 0
        await client.put('/api/settings', json={'vision_enabled': True})
        response = await client.post('/api/chat', json=payload)
        assert response.status_code == 200
        messages = (await client.get(f"/api/sessions/{session['id']}/messages")).json()
        assert messages[0]['attachments'][0]['id'] == image['id']
        assert not (await client.get(f"/api/sessions/{session['id']}/attachments")).json()
        assert (await client.delete('/api/media/' + image['id'])).status_code == 400
        await client.delete('/api/sessions/' + session['id'])
        assert (await client.get('/api/media/' + image['id'])).status_code == 404


async def test_attachments_cannot_cross_sessions_or_duplicate(setup):
    db, settings, app = setup
    settings.update({'vision_enabled': True})
    async with api(app) as client:
        first = (await client.post('/api/sessions', json={})).json()
        second = (await client.post('/api/sessions', json={})).json()
        image = (await client.post(f"/api/sessions/{first['id']}/attachments", files={'file': ('image.png', picture())})).json()
        response = await client.post('/api/chat', json={'session_id': second['id'], 'message': 'fixture', 'attachment_ids': [image['id']]})
        assert response.status_code == 400
        assert not db.messages(second['id'])
        response = await client.post('/api/chat', json={'session_id': first['id'], 'message': 'fixture', 'attachment_ids': [image['id'], image['id']]})
        assert response.status_code == 400 and not db.messages(first['id'])
        assert (await client.delete('/api/media/' + image['id'])).status_code == 200


async def test_pending_limit_and_file_validation(setup):
    db, settings, app = setup
    async with api(app) as client:
        session = (await client.post('/api/sessions', json={})).json()
        path = f"/api/sessions/{session['id']}/attachments"
        assert (await client.post(path, files={'file': ('fake.png', b'not an image')})).status_code == 400
        for i in range(4):
            assert (await client.post(path, files={'file': (f'{i}.png', picture())})).status_code == 200
        assert (await client.post(path, files={'file': ('5.png', picture())})).status_code == 400


async def test_tool_images_are_persisted_without_raw_payload_in_events(setup):
    db, settings, app = setup
    session = db.create_session()
    media = MediaStore(db)
    images, notices = await media.save_tool_images(session['id'], 'fixture_screenshot', [
        {'mime_type': 'image/png', 'data': base64.b64encode(picture()).decode()},
        {'mime_type': 'image/png', 'data': 'invalid base64'},
        {'mime_type': 'image/svg+xml', 'data': ''},
    ])
    assert len(images) == 1 and len(notices) == 2
    assert 'content' not in images[0] and 'data' not in images[0]
    assert media.data_url(images[0]['id'], session['id']).startswith('data:image/png;base64,')
    db.event(session['id'], {'type': 'tool_result', 'images': images})
    assert 'data:image' not in str(db.events(session['id']))


def test_visual_context_budget_excludes_base64_size():
    from backend.agent import context_size, limit_visual_context
    content = [{'type': 'text', 'text': 'hello'}] + [
        {'type': 'image_url', 'image_url': {'url': 'data:image/png;base64,' + 'A' * 100000}} for _ in range(12)]
    messages = [{'role': 'user', 'content': content}]
    bounded = limit_visual_context(messages, True)
    assert sum(p['type'] == 'image_url' for p in bounded[0]['content']) == 8
    assert context_size(bounded) < 12000
    disabled = limit_visual_context(messages, False)
    oversized = limit_visual_context(messages, True, maximum_chars=100)
    assert not any(p['type'] == 'image_url' for p in oversized[0]['content'])
    assert not any(p['type'] == 'image_url' for p in disabled[0]['content'])
