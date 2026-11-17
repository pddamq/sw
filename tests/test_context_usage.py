from types import SimpleNamespace as NS

from backend.context_usage import context_usage


def test_counts_only_outgoing_content_and_omits_image_payload():
    messages = [
        {'role': 'system', 'content': 'System instructions'},
        {'role': 'user', 'content': [
            {'type': 'text', 'text': 'Inspect this image'},
            {'type': 'image_url', 'image_url': {'url': 'data:image/png;base64,' + 'a' * 100_000}},
        ]},
    ]
    tools = [{'type': 'function', 'function': {'name': 'fixture'}}]
    result = context_usage(messages, tools)
    assert result['total'] == result['system'] + result['tools'] + result['messages']
    assert result['total'] < 200
    assert result['images'] == 1
    assert result['source'] == 'estimated'
    assert result['tools'] > 0
    assert len(messages[1]['content']) == 2
    assert 'System instructions' not in str(result)


def test_empty_context_and_unicode():
    assert context_usage([], [])['total'] == 0
    assert context_usage([{'role': 'user', 'content': '\u8bbe\u8ba1\u9700\u6c42'}], [])['messages'] > 0


async def test_agent_emits_usage_for_each_request_and_service_total(tmp_path, monkeypatch):
    import backend.agent as module
    from backend.agent import CadAgent
    from backend.config import SettingsStore
    from backend.knowledge import KnowledgeBase
    from backend.storage import Database
    from test_agent import FakeBridge, FakeClient, chunk

    monkeypatch.setenv('FORMA_API_KEY', 'fixture-key')
    db = Database(tmp_path / 'usage.db')
    settings = SettingsStore(tmp_path / 'settings.json')
    settings.update({'context_window': 128000, 'max_output_tokens': 4096})
    captures = []
    call = NS(index=0, id='c1', function=NS(name='sw_status', arguments='{}'))
    monkeypatch.setattr(module, 'AsyncOpenAI', lambda **kw: FakeClient([
        [chunk(calls=[call])], [chunk('Done'), NS(choices=[], usage=NS(prompt_tokens=321))],
    ], captures))
    session = db.create_session()
    db.add_message(session['id'], 'user', 'Check connection')
    events = []

    async def emit(event):
        events.append(event)

    await CadAgent(db, settings, FakeBridge(), KnowledgeBase(db, settings)).run(session['id'], 'Check connection', emit)
    usage = [event['usage'] for event in events if event['type'] == 'context_usage']
    assert [item['round'] for item in usage] == [1, 2, 2]
    for item, request in zip(usage[:2], captures):
        expected = context_usage(request['messages'], request.get('tools', []))
        assert item['total'] == expected['total']
        assert item['tools'] == expected['tools']
        assert item['model'] == request['model']
        assert item['limit'] == settings.value.context_window
        assert item['output_reserve'] == 4096
        assert item['compression_threshold'] == 102400
        assert request['max_tokens'] == settings.value.max_output_tokens
    assert usage[-1]['total'] == 321
    assert usage[-1]['source'] == 'reported'
