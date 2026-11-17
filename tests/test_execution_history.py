"""Status-window projection and append-only executor history paging."""
import json

from backend import execution_history


def test_status_projection_keeps_head_and_tail_and_explains_vba(tmp_path, monkeypatch):
    monkeypatch.setattr(execution_history, 'DATA_DIR', tmp_path)
    operations = [{'name': f'op_{index}', 'startedUtc': '2026-11-05T00:00:00Z',
                   'elapsedS': 1.0, 'status': 'completed'} for index in range(100)]
    status = {'attached': True, 'recentOps': operations, 'recentOpsTotal': 100,
              'recentOpsLimit': 1000, 'recentOpsTruncated': False, 'vbaEnabled': False}
    projected = execution_history.project_status(status)
    assert len(projected['recentOps']) == execution_history.STATUS_HEAD + execution_history.STATUS_TAIL
    assert projected['recentOps'][0]['name'] == 'op_0'
    assert projected['recentOps'][-1]['name'] == 'op_99'
    assert projected['recentOpsWindow']['omitted'] == 100 - execution_history.STATUS_HEAD - execution_history.STATUS_TAIL
    assert projected['recentOpsTruncated'] is True
    assert 'SWMCP_VBA=on' in projected['vbaNote']
    assert projected['retention']['fullHistoryTool'] == 'executor_history'
    assert projected['retention']['operationLog'].endswith('operations.jsonl')


def test_status_projection_leaves_existing_vba_note_and_small_window(tmp_path, monkeypatch):
    monkeypatch.setattr(execution_history, 'DATA_DIR', tmp_path)
    status = {'attached': False, 'recentOps': [{'name': 'one'}], 'recentOpsTotal': 1,
              'vbaEnabled': False, 'vbaNote': 'executor says no'}
    projected = execution_history.project_status(status)
    assert projected['vbaNote'] == 'executor says no'
    assert projected['recentOpsWindow']['omitted'] == 0
    assert projected['recentOpsTruncated'] is False


def test_status_text_projection_keeps_invalid_content_unchanged():
    result = {'content': 'not json'}
    assert execution_history.project_status_text(result) == 'not json'


def test_history_pages_newest_first_with_name_filter(tmp_path, monkeypatch):
    monkeypatch.setattr(execution_history, 'DATA_DIR', tmp_path)
    log = tmp_path / 'executor' / 'operations.jsonl'
    log.parent.mkdir(parents=True)
    rows = [{'name': 'model_info', 'status': 'completed'},
            {'name': 'sketch_circle', 'status': 'completed'},
            {'name': 'model_info', 'status': 'failed', 'error': 'NOT_CONNECTED'},
            {'name': 'doc_new', 'status': 'completed'}]
    log.write_text('\n'.join(json.dumps(row) for row in rows) + '\n', encoding='utf-8')
    page = execution_history.read_history(offset=0, limit=2)
    assert page['total'] == 4
    assert [entry['name'] for entry in page['entries']] == ['doc_new', 'model_info']
    assert page['next_offset'] == 2
    older = execution_history.read_history(offset=page['next_offset'], limit=2)
    assert [entry['name'] for entry in older['entries']] == ['sketch_circle', 'model_info']
    assert older['next_offset'] is None
    filtered = execution_history.read_history(name='model_info', limit=10)
    assert filtered['matched'] == 2
    assert all(entry['name'] == 'model_info' for entry in filtered['entries'])
    assert filtered['entries'][0]['status'] == 'failed'


def test_history_reports_missing_log(tmp_path, monkeypatch):
    monkeypatch.setattr(execution_history, 'DATA_DIR', tmp_path)
    page = execution_history.read_history()
    assert page['entries'] == [] and page['total'] == 0
    assert 'No executor operation log' in page['note']


async def test_agent_exposes_history_tool_and_projects_sw_status(tmp_path, monkeypatch):
    from types import SimpleNamespace as NS
    import backend.agent as module
    from backend.config import SettingsStore
    from backend.knowledge import KnowledgeBase
    from backend.storage import Database
    from test_agent import FakeBridge, FakeClient, chunk

    monkeypatch.setattr(execution_history, 'DATA_DIR', tmp_path)
    log = tmp_path / 'executor' / 'operations.jsonl'
    log.parent.mkdir(parents=True)
    log.write_text(json.dumps({'name': 'doc_new', 'status': 'completed'}) + '\n', encoding='utf-8')
    monkeypatch.setenv('FORMA_API_KEY', 'fixture')
    db = Database(tmp_path / 'agent.db')
    settings = SettingsStore(tmp_path / 'settings.json')
    session = db.create_session()
    db.add_message(session['id'], 'user', 'Check history and status')
    bridge = FakeBridge()
    bridge.tools = [
        {'name': 'sw_status', 'description': 'status',
         'inputSchema': {'type': 'object', 'properties': {}, 'additionalProperties': False}},
    ]
    operations = [{'name': f'op_{index}', 'status': 'completed'} for index in range(60)]
    async def result(name, arguments):
        bridge.calls.append((name, arguments))
        if name == 'sw_status':
            return {'content': json.dumps({'attached': True, 'recentOps': operations,
                                           'recentOpsTotal': 60, 'vbaEnabled': False}),
                    'is_error': False}
        return {'content': '{}', 'is_error': False}
    bridge.call = result
    calls = [NS(index=0, id='history', function=NS(name='executor_history',
                                                  arguments=json.dumps({'offset': 0, 'limit': 5}))),
             NS(index=1, id='status', function=NS(name='sw_status', arguments='{}'))]
    captures, events = [], []
    monkeypatch.setattr(module, 'AsyncOpenAI', lambda **kw: FakeClient(
        [[chunk(calls=calls)], [chunk('Done.')]], captures))
    async def emit(event):
        events.append(event)
    await module.CadAgent(db, settings, bridge, KnowledgeBase(db, settings)).run(
        session['id'], 'Check history and status', emit)
    history = [e for e in events if e['type'] == 'tool_result' and e['name'] == 'executor_history'][0]
    assert json.loads(history['content'])['entries'][0]['name'] == 'doc_new'
    status = [e for e in events if e['type'] == 'tool_result' and e['name'] == 'sw_status'
              and e.get('batch_index')][0]
    payload = json.loads(status['content'])
    assert payload['recentOpsWindow']['omitted'] == 60 - execution_history.STATUS_HEAD - execution_history.STATUS_TAIL
    assert 'SWMCP_VBA=on' in payload['vbaNote']
