"""Bounded local diagnostics, separate from chat history and model context."""
from __future__ import annotations

import json
import logging
import re
from datetime import datetime, timezone
from urllib.parse import urlsplit, urlunsplit

MAX_RECORD_BYTES = 65536
MAX_SESSION_RECORDS = 256
MAX_TOTAL_RECORDS = 1024
REDACTED = '[redacted]'
SENSITIVE = {'authorization', 'proxyauthorization', 'apikey', 'key', 'token', 'accesstoken',
             'refreshtoken', 'password', 'secret', 'clientsecret', 'cookie', 'setcookie',
             'headers', 'credentials'}


def sanitize(value, secrets=(), depth=0):
    """Redact before persistence, including JSON embedded in tool argument strings."""
    if depth > 16:
        return '[depth limit]'
    if isinstance(value, dict):
        result = {}
        for index, (key, child) in enumerate(value.items()):
            if index >= 200:
                result['_diagnostic_truncated'] = True
                break
            label = sanitize(str(key), secrets, depth + 1)
            normalized = re.sub(r'[^a-z]', '', str(key).lower())
            if normalized in {'data', 'content'} and (value.get('type') == 'image' or
                    str(value.get('mimeType', value.get('mime', ''))).startswith('image/')):
                result[label] = '[binary data omitted]'
            else:
                result[label] = REDACTED if normalized in SENSITIVE else sanitize(child, secrets, depth + 1)
        return result
    if isinstance(value, (list, tuple)):
        result = [sanitize(item, secrets, depth + 1) for item in value[:200]]
        if len(value) > 200:
            result.append('[items truncated]')
        return result
    if isinstance(value, bytes):
        return '[binary data omitted]'
    if not isinstance(value, str):
        return value if value is None or isinstance(value, (int, float, bool)) else sanitize(str(value), secrets, depth + 1)
    text = value
    for secret in sorted({s for s in secrets if isinstance(s, str) and s}, key=len, reverse=True):
        text = text.replace(secret, REDACTED)
    text = re.sub(r'\bsk-[A-Za-z0-9_-]+', REDACTED, text)
    text = re.sub(r'(?i)\bBearer\s+[^\s"\'<>]+', 'Bearer ' + REDACTED, text)
    text = re.sub(r'data:[^\s;,]+;base64,[A-Za-z0-9+/=\r\n]+', '[binary data omitted]', text)

    def clean_url(match):
        raw = match.group(0)
        try:
            parsed = urlsplit(raw)
            host = parsed.netloc.rsplit('@', 1)[-1]
            return urlunsplit((parsed.scheme, host, parsed.path,
                               'redacted' if parsed.query else '', ''))
        except ValueError:
            return '[URL omitted]'

    text = re.sub(r'https?://[^\s"\'<>]+', clean_url, text)
    # Tool arguments and upstream error bodies often contain nested JSON text.
    if text.lstrip().startswith(('{', '[')):
        try:
            parsed = json.loads(text)
            if isinstance(parsed, (dict, list)):
                cleaned = json.dumps(sanitize(parsed, secrets, depth + 1), ensure_ascii=False)
                return cleaned[:16000] + ('\n[diagnostic text truncated]' if len(cleaned) > 16000 else '')
        except (ValueError, RecursionError):
            pass
    text = re.sub(
        r'(?i)(\b(?:api[_-]?key|access[_-]?token|refresh[_-]?token|password|secret|authorization|cookie)'
        r'["\']?\s*[:=]\s*)(?:"[^"\r\n]*"|\'[^\'\r\n]*\'|[^\s,;}]+)',
        lambda match: match.group(1) + REDACTED, text)
    if len(text) > 16000:
        text = text[:16000] + '\n[diagnostic text truncated]'
    return text


def request_snapshot(data: dict, secrets=()) -> dict:
    """Preserve routing evidence independently of large messages and tool schemas."""
    request = data.get('request', {})
    tools = request.get('tools', [])

    def bounded(value, size=256):
        clean = sanitize(value, secrets)
        if isinstance(clean, str):
            return clean.encode('utf-8')[:size].decode('utf-8', errors='ignore')
        return clean if clean is None or isinstance(clean, (bool, int, float)) else '[non-scalar omitted]'

    names = [tool.get('function', {}).get('name', '') for tool in tools[:200]]
    choice = request.get('tool_choice')
    if isinstance(choice, dict):
        choice = {'type': bounded(choice.get('type')),
                  'function': {'name': bounded(choice.get('function', {}).get('name'))}}
    else:
        choice = bounded(choice)
    return {
        'round': bounded(data.get('round')), 'attempt': bounded(data.get('attempt')),
        'base_url': bounded(data.get('base_url'), 512),
        'protocol': bounded(data.get('protocol', 'chat')),
        'model': bounded(request.get('model')), 'tool_choice': choice,
        'max_tokens': bounded(request.get('max_tokens')),
        'temperature': bounded(request.get('temperature')),
        'stream': bounded(request.get('stream')),
        'reasoning_effort': bounded(request.get('reasoning_effort')),
        'message_count': len(request.get('messages', [])),
        'tool_count': len(tools),
        'tool_names': [bounded(name, 128) for name in names],
        'tool_names_truncated': len(tools) > 200 or any(
            len(str(name).encode('utf-8')) > 128 for name in names),
    }


def ensure_diagnostics(db):
    with db.connect() as conn:
        conn.executescript('''
            CREATE TABLE IF NOT EXISTS diagnostic_records (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id TEXT NOT NULL,
                task_id TEXT,
                kind TEXT NOT NULL,
                created_at TEXT NOT NULL,
                payload TEXT NOT NULL,
                FOREIGN KEY(session_id) REFERENCES sessions(id) ON DELETE CASCADE
            );
            CREATE INDEX IF NOT EXISTS diagnostic_session
                ON diagnostic_records(session_id,id);
        ''')


class DiagnosticRecorder:
    """Best-effort, task-scoped writer; diagnostic failures never repeat CAD calls."""

    def __init__(self, db, session_id, task_id=None, secrets=()):
        self.db = db
        self.session_id = session_id
        self.task_id = task_id
        self.secrets = tuple(secrets)
        self.failed = False
        try:
            ensure_diagnostics(db)
        except Exception:
            self.failed = True
            logging.getLogger(__name__).warning('Diagnostic storage initialization failed')

    def __call__(self, kind: str, data: dict):
        if self.failed:
            return
        try:
            snapshot = request_snapshot(data, self.secrets) if kind == 'model_request' else None
            if snapshot is not None:
                while len(json.dumps(snapshot, ensure_ascii=False).encode('utf-8')) > MAX_RECORD_BYTES // 2:
                    snapshot['tool_names'].pop()
                    snapshot['tool_names_truncated'] = True
            clean = sanitize(data, self.secrets)
            if snapshot is not None:
                clean['request_snapshot'] = snapshot
            payload = json.dumps(clean, ensure_ascii=False, allow_nan=False)
            if len(payload.encode('utf-8')) > MAX_RECORD_BYTES:
                # Metadata remains structured even when the full request exceeds retention limits.
                preview = payload.encode('utf-8')[:MAX_RECORD_BYTES // 4].decode('utf-8', errors='ignore')
                bounded = {'truncated': True, 'payload_preview': preview}
                if snapshot is not None:
                    bounded['request_snapshot'] = snapshot
                for field in ('round', 'attempt'):
                    if isinstance(clean.get(field), (int, float)):
                        bounded[field] = clean[field]
                payload = json.dumps(bounded, ensure_ascii=False, allow_nan=False)
                while len(payload.encode('utf-8')) > MAX_RECORD_BYTES:
                    bounded['payload_preview'] = bounded['payload_preview'][:len(bounded['payload_preview']) // 2]
                    payload = json.dumps(bounded, ensure_ascii=False, allow_nan=False)
            stamp = datetime.now(timezone.utc).isoformat()
            with self.db.connect() as conn:
                conn.execute(
                    'INSERT INTO diagnostic_records(session_id,task_id,kind,created_at,payload) VALUES(?,?,?,?,?)',
                    (self.session_id, self.task_id, kind[:80], stamp, payload))
                conn.execute('''DELETE FROM diagnostic_records WHERE session_id=? AND id NOT IN
                    (SELECT id FROM diagnostic_records WHERE session_id=? ORDER BY id DESC LIMIT ?)''',
                    (self.session_id, self.session_id, MAX_SESSION_RECORDS))
                conn.execute('''DELETE FROM diagnostic_records WHERE id NOT IN
                    (SELECT id FROM diagnostic_records ORDER BY id DESC LIMIT ?)''', (MAX_TOTAL_RECORDS,))
        except Exception:
            self.failed = True
            # Avoid logging exception text, which could itself contain credentials.
            logging.getLogger(__name__).warning('Diagnostic recording stopped after a storage or serialization error')


def export_diagnostics(db, session_id: str) -> dict:
    ensure_diagnostics(db)
    with db.connect() as conn:
        rows = conn.execute(
            'SELECT id,task_id,kind,created_at,payload FROM diagnostic_records WHERE session_id=? ORDER BY id',
            (session_id,)).fetchall()
    return {
        'format': 'forma-diagnostics-v1',
        'session_id': session_id,
        'exported_at': datetime.now(timezone.utc).isoformat(),
        'retention': {'per_session_records': MAX_SESSION_RECORDS, 'global_records': MAX_TOTAL_RECORDS,
                      'max_record_bytes': MAX_RECORD_BYTES,
                      'note': 'Oldest records are pruned. Large fields are truncated and binary image data is omitted.'},
        'privacy_note': 'Credentials are redacted on capture. Conversation text, model output, tool arguments and local paths may remain; review before sharing.',
        'records': [{'id': row['id'], 'task_id': row['task_id'], 'kind': row['kind'],
                     'created_at': row['created_at'], 'data': json.loads(row['payload'])} for row in rows],
    }
