"""Regression for explanatory inline calls after a real NOT_CONNECTED result.

Synthetic fixtures reproduce the diagnostic sequence without contacting a provider
or launching CAD. Text is never promoted to a tool call.
"""
import json

import pytest

from backend.agent import CadAgent
from backend.config import Settings, SettingsStore
from backend.knowledge import KnowledgeBase
from backend.storage import Database
from test_agent import FakeBridge
from test_native_model_protocols import install_transport, sse, responses_rows, claude_rows


@pytest.mark.parametrize('protocol', ['responses', 'claude'])
async def test_disconnected_result_can_be_explained_without_forced_repair(tmp_path, monkeypatch, protocol):
    import backend.agent as module
    monkeypatch.setenv('FORMA_API_KEY', 'fixture')
    settings = SettingsStore(tmp_path / 'settings.json')
    settings.value = Settings(protocol=protocol, base_url='https://fixture.test')
    db = Database(tmp_path / 'regression.db')
    sid = db.create_session()['id']
    db.add_message(sid, 'user', 'Check the connection before testing.')
    explanation = ('SOLIDWORKS 没有运行，doc_list 返回 NOT_CONNECTED。'
                   '`sw_connect(launch=true)` 会启动程序；本轮没有启动或修改文档。')

    class Bridge(FakeBridge):
        tools = [*FakeBridge.tools,
                 {'name': 'doc_list', 'description': 'List documents', 'inputSchema': {
                     'type': 'object', 'properties': {}, 'additionalProperties': False}},
                 {'name': 'sw_connect', 'description': 'Connect to CAD', 'inputSchema': {
                     'type': 'object', 'properties': {'launch': {'type': 'boolean'}},
                     'additionalProperties': False}}]

        async def call(self, name, arguments):
            if name == 'doc_list':
                self.calls.append((name, arguments))
                return {'content': 'NOT_CONNECTED: SOLIDWORKS is not running.', 'is_error': True}
            assert name != 'sw_connect', 'Documentation must never become a CAD operation'
            return await super().call(name, arguments)

    requests, events = [], []
    rows = responses_rows if protocol == 'responses' else claude_rows

    def handler(request):
        body = json.loads(request.content)
        requests.append(body)
        assert len(requests) <= 2, 'Explanatory prose must not enter a repair loop'
        if len(requests) == 1:
            return sse(rows(call={'call_id': 'list_once', 'name': 'doc_list', 'arguments': '{}'}))
        return sse(rows(text=explanation))

    install_transport(monkeypatch, handler)
    bridge = Bridge()

    async def emit(event):
        events.append(event)

    await CadAgent(db, settings, bridge, KnowledgeBase(db, settings)).run(sid, 'Check connection', emit)
    assert len(requests) == 2
    assert [name for name, _ in bridge.calls].count('doc_list') == 1
    assert not any(name == 'sw_connect' for name, _ in bridge.calls)
    assert db.messages(sid)[-1]['content'].strip() == explanation
    assert not any(e.get('phase') == 'rejected' for e in events)
    assert any(e['type'] == 'tool_result' and e.get('is_error') for e in events)
