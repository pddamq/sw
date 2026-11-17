import json
from types import SimpleNamespace as NS

import pytest

from backend.agent import CadAgent
from backend.config import SettingsStore
from backend.knowledge import KnowledgeBase
from backend.knowledge_tools import TOOLS, call
from backend.storage import Database
from test_agent import FakeBridge, FakeClient, chunk


@pytest.fixture
def library(tmp_path):
    db = Database(tmp_path / 'library.db')
    config = SettingsStore(tmp_path / 'settings.json')
    return db, config, KnowledgeBase(db, config)


async def test_library_pages_and_empty_search_do_not_hide_files(library):
    db, _, kb = library
    document = db.add_document('fixture.txt', [f'Paragraph {i}' for i in range(12)], None, '')
    for i in range(22):
        db.add_document(f'other-{i}.txt', ['unrelated'], None, '')
    first, _ = await call(kb, 'knowledge_list', {})
    assert first['total'] == 23 and len(first['documents']) == 20
    second, _ = await call(kb, 'knowledge_list', {'offset': first['next_offset']})
    assert len(second['documents']) == 3 and second['next_offset'] is None
    assert {d['id'] for d in first['documents']}.isdisjoint(d['id'] for d in second['documents'])
    filtered, _ = await call(kb, 'knowledge_list', {'query': 'fixture.txt'})
    assert [d['id'] for d in filtered['documents']] == [document['id']]
    empty, sources = await call(kb, 'knowledge_search', {'query': '不存在的关键词'})
    assert empty['matches'] == sources == []
    texts, offset = [], 0
    while offset is not None:
        page, sources = await call(kb, 'knowledge_read', {'document_id': document['id'], 'offset': offset})
        assert all(s['document_id'] == document['id'] and s['chunk_id'] for s in sources)
        texts.extend(c['text'] for c in page['chunks'])
        offset = page['next_offset']
    assert texts == [f'Paragraph {i}' for i in range(12)]
    db.delete_document(document['id'])
    with pytest.raises(ValueError, match='不存在'):
        await call(kb, 'knowledge_read', {'document_id': document['id']})


async def test_search_provides_stable_chunk_ids(library):
    db, _, kb = library
    document = db.add_document('plate.txt', ['plate thickness', 'plate width'], None, '')
    result, sources = await call(kb, 'knowledge_search', {'query': 'plate'})
    assert len(sources) == 2 and len({s['chunk_id'] for s in sources}) == 2
    assert {s['document_id'] for s in sources} == {document['id']}
    assert result['matches'] == sources


async def test_model_reads_uploaded_file_without_cad_or_initial_search_hit(library, monkeypatch):
    import backend.agent as module
    db, config, kb = library
    monkeypatch.setenv('FORMA_API_KEY', 'fixture-key')
    document = db.add_document('reference.txt', [f'Fixture paragraph {i}' for i in range(6)], None, '')
    def tool(name, args, id):
        return chunk(calls=[NS(index=0, id=id, function=NS(name=name, arguments=json.dumps(args)))])
    captures = []
    responses = [
        [tool('knowledge_list', {}, 'list')],
        [tool('knowledge_search', {'query': 'notmatching'}, 'search')],
        [tool('knowledge_read', {'document_id': document['id']}, 'read1')],
        [tool('knowledge_read', {'document_id': document['id'], 'offset': 5}, 'read2')],
        [chunk('Read reference.txt, all six chunks.')],
    ]
    monkeypatch.setattr(module, 'AsyncOpenAI', lambda **kw: FakeClient(responses, captures))
    bridge = FakeBridge()
    bridge.running = False
    bridge.tools = []
    session = db.create_session()
    db.add_message(session['id'], 'user', 'notmatching')
    events = []
    async def emit(event):
        events.append(event)
    await CadAgent(db, config, bridge, kb).run(session['id'], 'notmatching', emit)
    assert bridge.calls == []
    assert {s['function']['name'] for s in captures[0]['tools']} == set(TOOLS) | {'request_clarification'}
    assert 'uploaded_library_document_count' in captures[0]['messages'][0]['content']
    assert 'sw_help topics' in captures[0]['messages'][0]['content']
    results = [e for e in events if e['type'] == 'tool_result']
    assert len(results) == 4 and not any(e['is_error'] for e in results)
    pages = [json.loads(e['content']) for e in results if e['name'] == 'knowledge_read']
    assert [p['next_offset'] for p in pages] == [5, None]
    refs = [s for e in events if e['type'] == 'sources' for s in e['sources']]
    assert len(refs) == 6
    assert 'Fixture paragraph 5' in captures[-1]['messages'][-1]['content']
    assert all(e.get('purpose') for e in events if e['type'] == 'tool_start')


async def test_invalid_read_never_falls_through_to_cad_or_filesystem(library, monkeypatch):
    import backend.agent as module
    db, config, kb = library
    monkeypatch.setenv('FORMA_API_KEY', 'fixture-key')
    db.add_document('reference.txt', ['fixture'], None, '')
    bad = NS(index=0, id='invalid', function=NS(name='knowledge_read', arguments='{"document_id":"C:/private.txt"}'))
    monkeypatch.setattr(module, 'AsyncOpenAI', lambda **kw: FakeClient([[chunk(calls=[bad])]] * 3, []))
    bridge = FakeBridge()
    bridge.running = False
    bridge.tools = []
    session = db.create_session()
    db.add_message(session['id'], 'user', 'read')
    events = []
    async def emit(event):
        events.append(event)
    with pytest.raises(RuntimeError, match='协议校验'):
        await CadAgent(db, config, bridge, kb).run(session['id'], 'read', emit)
    assert bridge.calls == []
    assert any(e['type'] == 'status' and '协议校验' in e['message'] for e in events)
    assert not any(e['type'] in {'tool_start', 'tool_result'} for e in events)
