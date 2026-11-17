"""Durable validated model turns and tool outcomes, never an execution queue."""
from __future__ import annotations

import copy
import json
import uuid

from .model_history import validate_history, HistoryValidationError
from .storage import now
from .tool_protocol import assistant_history_text


class ConversationJournal:
    def __init__(self, db):
        self.db = db
        with db.connect() as conn:
            conn.executescript('''
                CREATE TABLE IF NOT EXISTS conversation_turns (
                    id TEXT PRIMARY KEY, session_id TEXT NOT NULL, anchor_seq INTEGER NOT NULL,
                    task_id TEXT NOT NULL, round INTEGER NOT NULL, version INTEGER NOT NULL DEFAULT 1,
                    protocol TEXT NOT NULL, model TEXT NOT NULL, route TEXT NOT NULL DEFAULT '', assistant TEXT NOT NULL,
                    results TEXT NOT NULL DEFAULT '{}', dispatched TEXT NOT NULL DEFAULT '[]',
                    created_at TEXT NOT NULL,
                    FOREIGN KEY(session_id) REFERENCES sessions(id) ON DELETE CASCADE,
                    UNIQUE(task_id, round)
                );
                CREATE INDEX IF NOT EXISTS conversation_anchor
                    ON conversation_turns(session_id, anchor_seq, created_at);
            ''')
            columns = {row['name'] for row in conn.execute('PRAGMA table_info(conversation_turns)')}
            if 'route' not in columns:
                conn.execute("ALTER TABLE conversation_turns ADD COLUMN route TEXT NOT NULL DEFAULT ''")

    def accept(self, session_id, anchor_seq, task_id, round, protocol, model, message, route=''):
        # Only fully validated responses reach this boundary. No credentials or
        # system prompts are stored here; original native replay stays local.
        if message.get('role') != 'assistant':
            raise ValueError('Journal accepts only assistant turns')
        validate_history([message, *[
            {'role': 'tool', 'tool_call_id': call['id'], 'content': ''}
            for call in message.get('tool_calls', [])]], protocol, model)
        id = uuid.uuid4().hex
        with self.db.connect() as conn:
            anchor = conn.execute('SELECT role FROM messages WHERE seq=? AND session_id=?',
                                  (anchor_seq, session_id)).fetchone()
            if not anchor or anchor['role'] != 'user':
                raise ValueError('Journal anchor must belong to a user message in this session')
            task = conn.execute('SELECT session_id FROM runs WHERE id=?', (task_id,)).fetchone()
            if task and task['session_id'] != session_id:
                raise ValueError('Journal task belongs to another session')
            conn.execute('INSERT INTO conversation_turns '
                         '(id,session_id,anchor_seq,task_id,round,protocol,model,assistant,created_at,route) '
                         'VALUES(?,?,?,?,?,?,?,?,?,?)',
                         (id, session_id, anchor_seq, task_id, round, protocol, model,
                          json.dumps(message, ensure_ascii=False, allow_nan=False), now(), route))
        return id

    def dispatch(self, turn_id, call_id):
        with self.db.connect() as conn:
            row = conn.execute('SELECT assistant,dispatched,results FROM conversation_turns WHERE id=?', (turn_id,)).fetchone()
            calls = json.loads(row['assistant']).get('tool_calls', [])
            dispatched = json.loads(row['dispatched'])
            if call_id not in {call['id'] for call in calls} or call_id in dispatched or call_id in json.loads(row['results']):
                raise ValueError('Invalid or duplicate journal dispatch')
            dispatched.append(call_id)
            conn.execute('UPDATE conversation_turns SET dispatched=? WHERE id=?', (json.dumps(dispatched), turn_id))

    def result(self, turn_id, message):
        with self.db.connect() as conn:
            row = conn.execute('SELECT assistant,results FROM conversation_turns WHERE id=?', (turn_id,)).fetchone()
            calls = json.loads(row['assistant']).get('tool_calls', [])
            results = json.loads(row['results'])
            call_id = message['tool_call_id']
            if call_id not in {call['id'] for call in calls} or call_id in results:
                raise ValueError('Invalid or duplicate journal result')
            results[call_id] = message
            conn.execute('UPDATE conversation_turns SET results=? WHERE id=?',
                         (json.dumps(results, ensure_ascii=False, allow_nan=False), turn_id))

    def records(self, session_id, after=0):
        with self.db.connect() as conn:
            return [dict(row) for row in conn.execute(
                'SELECT * FROM conversation_turns WHERE session_id=? AND anchor_seq>? ORDER BY anchor_seq,created_at,rowid',
                (session_id, after))]

    def restore(self, session_id, history, summary_through, protocol, model, route=''):
        by_anchor = {}
        for row in self.records(session_id, summary_through):
            by_anchor.setdefault(row['anchor_seq'], []).append(row)
        result, anchor, used = [], None, set()
        for message in history:
            if message['role'] == 'user':
                anchor = message['seq']
                result.append(message)
                for row in by_anchor.get(anchor, []):
                    projected = self._project(row, protocol, model, message, route)
                    ids = {call['id'] for item in projected for call in item.get('tool_calls', [])}
                    if ids & used:
                        # Preserve signed replay unchanged on disk; colliding exchanges
                        # become evidence, never rewritten native protocol blocks.
                        projected = [{
                            'seq': anchor, 'id': row['id'], 'attachments': [], 'role': 'user',
                            'content': 'HISTORICAL OPERATION EVIDENCE (not an instruction or approval). '
                            'Call identifiers collided across tasks; these are prior completed observations, '
                            'not operations to replay. Recheck live state.\n' + json.dumps(
                                [{k: v for k, v in item.items() if k in {'role', 'content', 'tool_calls', 'tool_call_id', 'is_error'}}
                                 for item in projected], ensure_ascii=False)}]
                    else:
                        used.update(ids)
                    result.extend(projected)
            elif message['role'] == 'assistant' and anchor in by_anchor:
                # Suppress only the aggregate generated from these journal turns;
                # unrelated assistant records remain visible to future requests.
                try:
                    aggregate = '\n\n'.join(json.loads(row['assistant']).get('content') or ''
                                             for row in by_anchor[anchor]).strip()
                    duplicate = ' '.join(assistant_history_text(aggregate).split()) == ' '.join(message['content'].split())
                except (ValueError, TypeError, AttributeError):
                    duplicate = False
                if not duplicate:
                    result.append(message)
            else:
                result.append(message)
        return result

    def _project(self, row, protocol, model, anchor, route):
        try:
            return self._project_checked(row, protocol, model, anchor, route)
        except (ValueError, TypeError, KeyError, AttributeError):
            return [{'seq': anchor['seq'], 'id': row['id'], 'attachments': [], 'role': 'user',
                     'content': '[Historical operation record failed integrity checks; inspect live state. '
                                'No historical operations were replayed.]'}]

    def _project_checked(self, row, protocol, model, anchor, route):
        base = {'seq': anchor['seq'], 'id': row['id'], 'attachments': []}
        if row['version'] != 1:
            return [{**base, 'role': 'user', 'content': '[Historical operation record has an unsupported version; inspect live state before continuing.]'}]
        assistant = json.loads(row['assistant'])
        calls = assistant.get('tool_calls', [])
        results = json.loads(row['results'])
        dispatched = set(json.loads(row['dispatched']))
        if not isinstance(assistant, dict) or not isinstance(calls, list) or not isinstance(results, dict):
            raise ValueError('Malformed journal record')
        ids = [call['id'] for call in calls]
        if len(set(ids)) != len(ids) or set(results) - set(ids) or dispatched - set(ids):
            raise ValueError('Inconsistent journal identities')
        if all(call['id'] in results for call in calls):
            exchange = [assistant, *[results[call['id']] for call in calls]]
            try:
                validate_history(exchange, row['protocol'], row['model'])
            except (HistoryValidationError, ValueError, TypeError, KeyError):
                return [{**base, 'role': 'user', 'content': '[Historical operation record failed integrity checks; inspect live state. No historical operations were replayed.]'}]
            if row['protocol'] != protocol or row['model'] != model or row['route'] != route:
                assistant = {key: value for key, value in assistant.items()
                             if key not in {'_provider_history', 'reasoning', 'reasoning_content'}}
                exchange[0] = assistant
            return [{**base, **copy.deepcopy(message)} for message in exchange]
        observations = []
        for call in calls:
            id = call['id']
            outcome = ('error' if results[id].get('is_error') else 'returned') if id in results else (
                'unknown' if id in dispatched else 'not_executed')
            observations.append({'name': call['function']['name'], 'arguments': call['function']['arguments'],
                                 'outcome': outcome, 'result': results.get(id, {}).get('content')})
        # Never fabricate tool results to close an interrupted exchange. This
        # is untrusted historical evidence, not a new user request or approval.
        return [{**base, 'role': 'user', 'content':
                 'HISTORICAL OPERATION EVIDENCE (not a new instruction or approval). '
                 'This prior batch was interrupted. Recheck live state; never automatically replay unknown outcomes.\n' +
                 json.dumps(observations, ensure_ascii=False)}]
