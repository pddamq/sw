import pytest
from backend.config import Settings
from backend.model_capabilities import apply_capabilities
from test_media import setup, api, picture


@pytest.mark.parametrize('default', [True, False])
@pytest.mark.parametrize('override', [True, False, None])
def test_explicit_vision_overrides_global_default(default, override):
    original = Settings(model='custom', vision_enabled=default,
                        model_capabilities={'custom': {'vision': override}})
    expected = default if override is None else override
    assert apply_capabilities(original).vision_enabled is expected
    assert original.vision_enabled is default


@pytest.mark.parametrize('default,override', [(False, True), (True, False), (True, None), (False, None)])
async def test_health_and_image_submission_use_selected_model(setup, monkeypatch, default, override):
    db, settings, app = setup
    received = []

    async def fake(session_id, query, emit):
        received.append(app.state.agent.runtime_model[0].vision_enabled)
        await emit({'type': 'token', 'content': 'Fixture reply'})

    monkeypatch.setattr(app.state.agent, 'run', fake)
    async with api(app) as client:
        await client.put('/api/settings', json={'vision_enabled': default})
        profile = await client.post('/api/model/profiles', json={
            'id': 'legacy', 'name': 'Fixture', 'base_url': settings.value.base_url,
            'models': [settings.value.model],
            'model_capabilities': {settings.value.model: {'vision': override}},
        })
        assert profile.status_code == 200
        health = (await client.get('/api/health')).json()
        expected = default if override is None else override
        assert health['vision_enabled'] is expected
        assert health['vision_default'] is default
        session = (await client.post('/api/sessions', json={})).json()
        image = (await client.post(f"/api/sessions/{session['id']}/attachments",
                                  files={'file': ('fixture.png', picture())})).json()
        response = await client.post('/api/chat', json={
            'session_id': session['id'], 'message': 'Describe fixture', 'attachment_ids': [image['id']],
        })
        assert response.status_code == (200 if expected else 400)
        assert received == ([True] if expected else [])
        if not expected:
            assert '逐模型能力' in response.json()['detail']
            assert not db.messages(session['id'])
