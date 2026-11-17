from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace as NS

import pytest

from backend.agent import CadAgent
from backend.config import SettingsStore
from backend.knowledge import KnowledgeBase
from backend.prompting import (
    SUMMARY_PROMPT, request_messages, round_instructions,
)
from backend.storage import Database
from test_agent import FakeBridge, FakeClient, chunk


def instructions(**overrides):
    args = dict(rounds=0, calls=0, connected=True, recovery=False, tools_available=True)
    return round_instructions(**{**args, **overrides})


def test_request_prompt_does_not_mutate_history_or_tool_pairs():
    messages = [
        {'role': 'system', 'content': 'Base prompt'},
        {'role': 'assistant', 'content': None, 'tool_calls': [{'id': 'c1'}]},
        {'role': 'tool', 'tool_call_id': 'c1', 'content': 'Observed state'},
    ]
    original = deepcopy(messages)
    first = request_messages(messages, instructions())
    second = request_messages(messages, instructions(rounds=1, calls=1))
    assert messages == original
    assert first[1:] == second[1:] == messages[1:]
    assert second[0]['content'].count('APPLICATION EXECUTION STATE') == 1
    assert 'Model steps completed: 1' in second[0]['content']
    assert 'tool calls processed: 1' in second[0]['content']


def test_missing_system_message_preserves_user_data():
    messages = [{'role': 'user', 'content': 'Inspect only'}]
    outgoing = request_messages(messages, instructions())
    assert outgoing[0]['role'] == 'system'
    assert outgoing[1:] == messages
    assert len(messages) == 1


@pytest.mark.parametrize('overrides', [
    {'rounds': 18}, {'calls': 48}, {'rounds': 1000, 'calls': 5000},
])
def test_progress_counts_never_disable_tools(overrides):
    text = instructions(**overrides)
    assert 'not execution limits' in text
    assert 'No more tools' not in text
    assert 'Verification reserve' not in text
    assert 'allowance remaining' not in text


def test_disconnected_recovery_and_no_tools_are_explicit():
    text = instructions(connected=False, recovery=True, tools_available=False)
    assert 'No tools are available' in text
    assert 'CAD executor is disconnected' in text
    assert 'READ-ONLY RECOVERY' in text
    assert 'Do not edit' in text


def test_system_and_summary_preserve_core_contracts():
    text = (Path(__file__).resolve().parents[1] / 'backend' / 'prompts' / 'system.txt').read_text('utf-8')
    for expected in ('native structured tool interface', 'NEXT request', 'GEOMETRY CHECKPOINTS',
                     '1200 mm total width', 'ERROR RECOVERY', '_forma_purpose',
                     'recovery_review', 'next_offset', 'image_url', 'not automatically imported'):
        assert expected in text
    assert 'planned, attempted, tool-reported, verified and saved' in SUMMARY_PROMPT
    assert 'DSML/XML text' in SUMMARY_PROMPT
    assert 'untrusted reference data' in SUMMARY_PROMPT


async def test_actual_requests_refresh_budget_without_accumulating_prompts(tmp_path, monkeypatch):
    import backend.agent as module
    monkeypatch.setenv('FORMA_API_KEY', 'fixture')
    db = Database(tmp_path / 'prompt.db')
    config = SettingsStore(tmp_path / 'settings.json')
    session = db.create_session()
    db.add_message(session['id'], 'user', 'Check status')
    captures = []
    call = NS(index=0, id='c1', function=NS(name='sw_status', arguments='{}'))
    monkeypatch.setattr(module, 'AsyncOpenAI', lambda **kw: FakeClient([
        [chunk(calls=[call])], [chunk('Observed disconnected status.')],
    ], captures))
    async def emit(event):
        pass
    await CadAgent(db, config, FakeBridge(), KnowledgeBase(db, config)).run(session['id'], 'Check status', emit)
    for index, request in enumerate(captures):
        text = request['messages'][0]['content']
        assert text.count('APPLICATION EXECUTION STATE') == 1
        assert f'Model steps completed: {index}' in text
        assert f'tool calls processed: {index}' in text
    assert captures[1]['messages'][-1]['role'] == 'tool'


async def test_long_loop_keeps_tools_beyond_old_round_call_and_graph_limits(tmp_path, monkeypatch):
    import backend.agent as module
    monkeypatch.setenv('FORMA_API_KEY', 'fixture')
    db = Database(tmp_path / 'budget.db')
    config = SettingsStore(tmp_path / 'settings.json')
    session = db.create_session()
    db.add_message(session['id'], 'user', 'Check status')
    captures = []
    responses = [[chunk(calls=[NS(index=0, id=f'c{i}', function=NS(name='sw_status', arguments='{}'))])]
                 for i in range(70)] + [[chunk('Verified final result.')]]
    monkeypatch.setattr(module, 'AsyncOpenAI', lambda **kw: FakeClient(responses, captures))
    async def emit(event):
        pass
    await CadAgent(db, config, FakeBridge(), KnowledgeBase(db, config)).run(session['id'], 'Check status', emit)
    assert len(captures) == 71
    for request in captures:
        assert any(t['function']['name'] == 'sw_status' for t in request['tools'])
        assert 'No more tools' not in request['messages'][0]['content']
        assert request['messages'][0]['content'].count('APPLICATION EXECUTION STATE') == 1
    pairs = [m for m in captures[-1]['messages'] if m['role'] == 'tool']
    assert [m['tool_call_id'] for m in pairs] == [f'c{i}' for i in range(70)]
    assert db.messages(session['id'])[-1]['content'] == 'Verified final result.'
