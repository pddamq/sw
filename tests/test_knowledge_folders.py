from __future__ import annotations

import httpx
import pytest

from backend.app import create_app
from backend.config import SettingsStore
from backend.knowledge import document_path, KnowledgeBase
from backend.storage import Database


@pytest.mark.parametrize('value', [
    '', '/root/a.md', '../a.md', 'root/../a.md', 'root/./a.md', 'root//a.md',
    'C:/root/a.md', '\\\\server\\a.md', 'root/a\x00.md', 'root/a\n.md',
    'root/', 'a' * 256 + '.md', '/'.join(['dir'] * 65) + '/a.md',
])
def test_invalid_relative_paths(value):
    with pytest.raises(ValueError):
        document_path(value)


def test_normalize_nested_paths_without_truncation():
    path = '工程资料\\设计规范\\底板.md'
    assert document_path(path) == '工程资料/设计规范/底板.md'
    long_path = '/'.join(['a' * 100] * 3) + '/spec.md'
    assert document_path(long_path) == long_path


async def test_folder_paths_persist_and_are_used_by_retrieval(tmp_path):
    db_path = tmp_path / 'test.db'
    db = Database(db_path)
    settings = SettingsStore(tmp_path / 'settings.json')
    app = create_app(db, settings, token='fixture')
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://testserver',
                                headers={'Authorization': 'Bearer fixture'}) as client:
        paths = ['工程资料/设计/spec.md', '工程资料/API/spec.md', '其他/spec.md']
        ids = []
        for path in paths:
            response = await client.post('/api/knowledge', files={'file': ('spec.md', b'Extrude fixture guidance')},
                                         data={'relative_path': path})
            assert response.status_code == 200
            assert response.json()['name'] == path
            ids.append(response.json()['id'])
        assert len(set(ids)) == 3
        legacy = await client.post('/api/knowledge', files={'file': ('plain.md', b'Legacy fixture')})
        assert legacy.status_code == 200 and legacy.json()['name'] == 'plain.md'
        invalid = await client.post('/api/knowledge', files={'file': ('spec.md', b'fixture')},
                                    data={'relative_path': '../escape.md'})
        assert invalid.status_code == 400
        unsupported = await client.post('/api/knowledge', files={'file': ('image.png', b'fixture')},
                                        data={'relative_path': '工程资料/image.png'})
        assert unsupported.status_code == 400
        listed = (await client.get('/api/knowledge')).json()
        assert {doc['name'] for doc in listed} == {*paths, 'plain.md'}
        preview = await client.post('/api/knowledge/preview', json={'query': 'Extrude fixture'})
        assert {source['name'] for source in preview.json()['sources']} >= set(paths)
        reloaded = Database(db_path)
        assert {doc['name'] for doc in reloaded.knowledge()} == {*paths, 'plain.md'}
        assert reloaded.document_page(ids[0])['name'] == paths[0]
        assert reloaded.knowledge_page(query='工程资料/设计')['documents'][0]['id'] == ids[0]
        assert (await client.delete('/api/knowledge/' + ids[0])).status_code == 200
        assert {doc['name'] for doc in db.knowledge()} == {paths[1], paths[2], 'plain.md'}
        assert all(row['document_id'] != ids[0] for row in db.chunks())


async def test_same_path_imports_are_non_destructive(tmp_path):
    db = Database(tmp_path / 'test.db')
    kb = KnowledgeBase(db, SettingsStore(tmp_path / 'settings.json'))
    first = await kb.ingest('root/spec.md', b'Original text')
    second = await kb.ingest('root/spec.md', b'Updated text')
    assert first['id'] != second['id']
    assert len(db.knowledge()) == 2
    assert db.document_page(first['id'])['chunks'][0]['text'] == 'Original text'
