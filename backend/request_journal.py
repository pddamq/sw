"""Bounded, redacted model request inspection; never an execution/replay queue."""
from __future__ import annotations

import hashlib
import json

from .diagnostics import sanitize
from .storage import now

MAX_REQUEST_BYTES = 2 * 1024 * 1024
MAX_REQUESTS = 200
MAX_GLOBAL_REQUESTS = 1000


def digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                     allow_nan=False).encode('utf-8')).hexdigest()


class RequestJournal:
    def __init__(self, db, secrets=()):
        self.db, self.secrets = db, secrets
        with db.connect() as conn:
            conn.executescript('''
                CREATE TABLE IF NOT EXISTS model_requests (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    session_id TEXT NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
                    task_id TEXT NOT NULL, round INTEGER NOT NULL, attempt_id TEXT NOT NULL,
                    created_at TEXT NOT NULL, metadata TEXT NOT NULL, snapshot TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS request_session ON model_requests(session_id,id);
            ''')

    def capture(self, session_id, task_id, round, attempt_id, protocol, route, options, context):
        # Sanitize before writing. This is the internal envelope, not an HTTP packet dump.
        clean = sanitize({'protocol': protocol, 'route': route, 'request': options,
                          'context': context}, self.secrets)
        raw = json.dumps(clean, ensure_ascii=False, allow_nan=False)
        omitted = []
        if clean != {'protocol': protocol, 'route': route, 'request': options, 'context': context}:
            omitted.append('Redaction, text/item/depth limits or image omission applied; not exact replay data.')
        fingerprint = digest(clean)
        original_bytes = len(raw.encode('utf-8'))
        if original_bytes > MAX_REQUEST_BYTES:
            omitted.append('Snapshot exceeded the byte limit; messages, tools and context omitted. Fingerprint covers the sanitized pre-omission snapshot.')
            clean = {'protocol': clean.get('protocol'), 'route': clean.get('route'),
                     'request': {key: value for key, value in clean.get('request', {}).items()
                                 if key not in {'messages', 'tools'}},
                     'context': '[Oversize context omitted; see fingerprint and linked diagnostic events.]'}
            raw = json.dumps(clean, ensure_ascii=False, allow_nan=False)
            if len(raw.encode('utf-8')) > MAX_REQUEST_BYTES:
                clean = {'omitted': 'Oversize request; metadata only.'}
                raw = json.dumps(clean)
        with self.db.connect() as conn:
            previous = conn.execute('SELECT id,metadata FROM model_requests WHERE session_id=? ORDER BY id DESC LIMIT 1',
                                    (session_id,)).fetchone()
            parts = {key: digest(sanitize(value, self.secrets)) for key, value in {
                'messages': options.get('messages', []), 'tools': options.get('tools', []),
                'configuration': {'protocol': protocol, 'route': route, **{
                    k: v for k, v in options.items() if k not in {'messages', 'tools'}}},
            }.items()}
            previous_meta = json.loads(previous['metadata']) if previous else {}
            safe_options = sanitize(options, self.secrets)
            configuration = {k: v for k, v in safe_options.items() if k not in {'messages', 'tools'}}
            configuration.update(protocol=protocol, route=sanitize(route, self.secrets))
            tool_versions = {tool['function']['name']: digest(tool['function'])
                             for tool in safe_options.get('tools', []) if isinstance(tool, dict) and 'function' in tool}
            message_versions = [digest(message) for message in safe_options.get('messages', [])]
            comparison = {'configuration': configuration, 'tools': tool_versions, 'messages': message_versions}
            prior = previous_meta.get('comparison', {})
            before_config, before_tools = prior.get('configuration', {}), prior.get('tools', {})
            prior_messages = prior.get('messages', [])
            prefix = 0
            for old, new in zip(prior_messages, message_versions):
                if old != new:
                    break
                prefix += 1
            differences = {
                'configuration': {k: {'before': before_config.get(k), 'after': configuration.get(k)}
                                  for k in sorted(before_config.keys() | configuration.keys())
                                  if before_config.get(k) != configuration.get(k)},
                'tools_added': sorted(tool_versions.keys() - before_tools.keys()),
                'tools_removed': sorted(before_tools.keys() - tool_versions.keys()),
                'tools_changed': sorted(k for k in tool_versions.keys() & before_tools.keys()
                                        if tool_versions[k] != before_tools[k]),
                'unchanged_message_prefix': prefix,
                'compared_messages': len(message_versions),
                'comparison_note': 'Differences are computed after redaction and inspection limits, not from unredacted wire bytes.'}
            metadata = {'protocol': protocol, 'model': sanitize(options.get('model'), self.secrets), 'fingerprint': fingerprint,
                        'comparison': comparison, 'differences': differences,
                        'parts': parts, 'previous_id': previous['id'] if previous else None,
                        'changed': [key for key in parts if parts[key] != previous_meta.get('parts', {}).get(key)],
                        'message_count': len(options.get('messages', [])), 'tool_count': len(options.get('tools', [])),
                        'omissions': omitted, 'sanitized_bytes': original_bytes,
                        'representation': 'internal normalized request; native protocol conversion occurs afterward'}
            cursor = conn.execute('INSERT INTO model_requests(session_id,task_id,round,attempt_id,created_at,metadata,snapshot) VALUES(?,?,?,?,?,?,?)',
                                  (session_id, task_id, round, attempt_id, now(), json.dumps(metadata), raw))
            id = cursor.lastrowid
            conn.execute('DELETE FROM model_requests WHERE session_id=? AND id NOT IN (SELECT id FROM model_requests WHERE session_id=? ORDER BY id DESC LIMIT ?)',
                         (session_id, session_id, MAX_REQUESTS))
            conn.execute('DELETE FROM model_requests WHERE id NOT IN (SELECT id FROM model_requests ORDER BY id DESC LIMIT ?)',
                         (MAX_GLOBAL_REQUESTS,))
        return id

    def page(self, session_id, before=None, limit=20):
        limit = max(1, min(limit, 50))
        with self.db.connect() as conn:
            rows = conn.execute('SELECT id,task_id,round,attempt_id,created_at,metadata FROM model_requests WHERE session_id=? AND (? IS NULL OR id<?) ORDER BY id DESC LIMIT ?',
                                (session_id, before, before, limit + 1)).fetchall()
        return {'records': [{**{k: row[k] for k in ('id', 'task_id', 'round', 'attempt_id', 'created_at')},
                             **{k: v for k, v in json.loads(row['metadata']).items() if k != 'comparison'}} for row in rows[:limit]],
                'next_before': rows[limit - 1]['id'] if len(rows) > limit else None,
                'retention': {'per_session': MAX_REQUESTS, 'global': MAX_GLOBAL_REQUESTS,
                              'max_request_bytes': MAX_REQUEST_BYTES},
                'notice': 'Older requests may be pruned; no credentials or image bytes are retained. Text and local paths may remain. Inspection only, never executes tools.'}

    def detail(self, session_id, id):
        with self.db.connect() as conn:
            row = conn.execute('SELECT * FROM model_requests WHERE session_id=? AND id=?', (session_id, id)).fetchone()
        if row is None:
            return None
        metadata = json.loads(row['metadata'])
        metadata.pop('comparison', None)
        return {**metadata, 'id': id, 'task_id': row['task_id'], 'round': row['round'],
                'attempt_id': row['attempt_id'], 'created_at': row['created_at'], 'snapshot': json.loads(row['snapshot'])}
