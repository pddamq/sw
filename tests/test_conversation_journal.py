"""Offline durable conversation and generation-attempt regressions."""
import json
import pytest

from backend.conversation_journal import ConversationJournal
from backend.generation_attempts import GenerationAttempts
from backend.model_history import validate_history
from backend.storage import Database


@pytest.fixture
def store(tmp_path):
    db = Database(tmp_path / 'history.db')
    sid = db.create_session()['id']
    db.add_message(sid, 'user', 'Inspect synthetic fixture')
    anchor = db.messages(sid)[0]['seq']
    return db, sid, anchor, ConversationJournal(db)


def message(ids=('a',)):
    return {'role': 'assistant', 'content': None, 'tool_calls': [
        {'id': id, 'type': 'function', 'function': {'name': 'inspect', 'arguments': '{}'}} for id in ids]}


def result(id='a'):
    return {'role': 'tool', 'tool_call_id': id, 'content': 'Observed fixture', 'is_error': False}


def accept(store, ids=('a',), **kwargs):
    db, sid, anchor, journal = store
    return journal.accept(sid, anchor, kwargs.get('task', 'task'), 1, 'chat', 'fixture', message(ids), 'https://fixture.invalid')


def restore(store, **kwargs):
    db, sid, anchor, journal = store
    return journal.restore(sid, db.messages(sid), 0, kwargs.get('protocol', 'chat'),
                           kwargs.get('model', 'fixture'), kwargs.get('route', 'https://fixture.invalid'))


def test_restart_restores_complete_exchange_without_reexecution(store):
    db, sid, anchor, journal = store
    turn = accept(store)
    journal.dispatch(turn, 'a')
    journal.result(turn, result())
    db.add_message(sid, 'assistant', '')
    restarted = ConversationJournal(Database(db.path))
    rows = restarted.restore(sid, db.messages(sid), 0, 'chat', 'fixture', 'https://fixture.invalid')
    assert [m['role'] for m in rows] == ['user', 'assistant', 'tool']
    validate_history(rows, 'chat', 'fixture')
    assert rows[-1]['content'] == 'Observed fixture'


def test_partial_batch_preserves_known_unknown_and_unstarted(store):
    turn = accept(store, ('a', 'b', 'c'))
    journal = store[-1]
    journal.dispatch(turn, 'a')
    journal.result(turn, result())
    journal.dispatch(turn, 'b')
    rows = restore(store)
    assert not any(m.get('tool_calls') for m in rows)
    evidence = json.loads(rows[-1]['content'].split('\n', 1)[1])
    assert [m['outcome'] for m in evidence] == ['returned', 'unknown', 'not_executed']
    validate_history(rows, 'chat', 'fixture')


@pytest.mark.parametrize('field,value', [('assistant', '{'), ('results', '[]'), ('dispatched', 'null'), ('version', 99)])
def test_corrupt_or_newer_records_become_inspection_evidence(store, field, value):
    turn = accept(store)
    with store[0].connect() as conn:
        conn.execute(f'UPDATE conversation_turns SET {field}=? WHERE id=?', (value, turn))
    rows = restore(store)
    assert rows[-1]['role'] == 'user'
    assert 'inspect live state' in rows[-1]['content']
    validate_history(rows, 'chat', 'fixture')


def test_duplicate_identifiers_across_tasks_become_evidence(store):
    db, sid, anchor, journal = store
    first = accept(store)
    journal.result(first, result())
    db.add_message(sid, 'user', 'Second task')
    second = journal.accept(sid, db.messages(sid)[-1]['seq'], 'second', 1, 'chat', 'fixture', message(), 'https://fixture.invalid')
    journal.result(second, result())
    rows = restore(store)
    validate_history(rows, 'chat', 'fixture')
    assert 'identifiers collided' in rows[-1]['content']
    assert len(journal.records(sid)) == 2


@pytest.mark.parametrize('change', [{'protocol': 'responses'}, {'model': 'other'}, {'route': 'https://other.invalid'}])
def test_native_signatures_stay_local_when_provider_identity_changes(store, change):
    db, sid, anchor, journal = store
    msg = {'role': 'assistant', 'content': 'Observed', '_provider_history': {
        'protocol': 'claude', 'model': 'fixture', 'items': [
            {'type': 'thinking', 'thinking': 'internal', 'signature': 'signed'},
            {'type': 'text', 'text': 'Observed'}]}}
    journal.accept(sid, anchor, 'native', 1, 'claude', 'fixture', msg, 'https://fixture.invalid')
    rows = restore(store, **{'protocol': 'claude', **change})
    assert '_provider_history' not in rows[-1]
    assert 'signature' in journal.records(sid)[0]['assistant']


def test_anchor_ownership_dispatch_uniqueness_and_cascade(store):
    db, sid, anchor, journal = store
    other = db.create_session()['id']
    with pytest.raises(ValueError, match='anchor'):
        journal.accept(other, anchor, 'invalid', 1, 'chat', 'fixture', message())
    turn = accept(store)
    journal.dispatch(turn, 'a')
    with pytest.raises(ValueError, match='duplicate'):
        journal.dispatch(turn, 'a')
    journal.result(turn, result())
    with pytest.raises(ValueError, match='duplicate'):
        journal.result(turn, result())
    db.delete_session(sid)
    assert journal.records(sid) == []


def test_legacy_route_column_migrates(store):
    db, sid, anchor, journal = store
    with db.connect() as conn:
        conn.execute('ALTER TABLE conversation_turns DROP COLUMN route')
    upgraded = ConversationJournal(db)
    turn = upgraded.accept(sid, anchor, 'upgraded', 1, 'chat', 'fixture', {'role': 'assistant', 'content': 'ok'})
    assert upgraded.records(sid)[0]['id'] == turn


async def test_attempts_have_unique_ids_monotonic_revisions_and_no_model_text():
    events = []
    async def emit(event):
        events.append(event)
    attempts = GenerationAttempts(emit)
    await attempts.start(1, 1)
    await attempts.receiving()
    await attempts.receiving()
    await attempts.update('retry_wait', delay_seconds=2)
    await attempts.start(1, 2)
    await attempts.update('committed')
    assert [e['phase'] for e in events] == ['waiting', 'receiving', 'retry_wait', 'waiting', 'committed']
    assert [e['revision'] for e in events] == [1, 2, 3, 4, 5]
    assert events[0]['attempt_id'] != events[-1]['attempt_id']
    assert all('content' not in e and e['elapsed_ms'] >= 0 for e in events)
