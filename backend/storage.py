from __future__ import annotations

import json
import os
import sqlite3
import threading
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

from .config import DATA_DIR


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


class Database:
    def __init__(self, path: Path = DATA_DIR / 'forma.db'):
        self.path = path
        self.lock = threading.RLock()
        with self.connect() as conn:
            conn.executescript('''
                PRAGMA journal_mode=WAL;
                CREATE TABLE IF NOT EXISTS sessions (
                    id TEXT PRIMARY KEY, title TEXT NOT NULL, updated_at TEXT NOT NULL,
                    summary TEXT NOT NULL DEFAULT '', summary_through INTEGER NOT NULL DEFAULT 0
                );
                CREATE TABLE IF NOT EXISTS messages (
                    seq INTEGER PRIMARY KEY AUTOINCREMENT, id TEXT UNIQUE, session_id TEXT NOT NULL,
                    role TEXT NOT NULL, content TEXT NOT NULL, created_at TEXT NOT NULL,
                    FOREIGN KEY(session_id) REFERENCES sessions(id) ON DELETE CASCADE
                );
                CREATE TABLE IF NOT EXISTS events (
                    id INTEGER PRIMARY KEY, session_id TEXT NOT NULL, payload TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    FOREIGN KEY(session_id) REFERENCES sessions(id) ON DELETE CASCADE
                );
                CREATE TABLE IF NOT EXISTS runs (
                    id TEXT PRIMARY KEY, session_id TEXT NOT NULL, owner_pid INTEGER NOT NULL,
                    status TEXT NOT NULL, message TEXT NOT NULL, detail TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL, updated_at TEXT NOT NULL, finished_at TEXT,
                    FOREIGN KEY(session_id) REFERENCES sessions(id) ON DELETE CASCADE
                );
                CREATE INDEX IF NOT EXISTS runs_session_time ON runs(session_id,created_at);
                CREATE TABLE IF NOT EXISTS checkpoints (
                    id INTEGER PRIMARY KEY AUTOINCREMENT, task_id TEXT NOT NULL,
                    name TEXT NOT NULL, arguments TEXT NOT NULL, status TEXT NOT NULL,
                    result TEXT NOT NULL DEFAULT '{}', snapshot TEXT NOT NULL DEFAULT '{}',
                    warning TEXT NOT NULL DEFAULT '', created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
                    FOREIGN KEY(task_id) REFERENCES runs(id) ON DELETE CASCADE
                );
                CREATE INDEX IF NOT EXISTS checkpoints_task ON checkpoints(task_id,id);
                CREATE TABLE IF NOT EXISTS media (
                    id TEXT PRIMARY KEY, session_id TEXT NOT NULL, message_id TEXT, task_id TEXT,
                    kind TEXT NOT NULL, name TEXT NOT NULL, mime TEXT NOT NULL,
                    width INTEGER NOT NULL, height INTEGER NOT NULL, size INTEGER NOT NULL,
                    created_at TEXT NOT NULL, content BLOB NOT NULL,
                    FOREIGN KEY(session_id) REFERENCES sessions(id) ON DELETE CASCADE,
                    FOREIGN KEY(message_id) REFERENCES messages(id) ON DELETE CASCADE,
                    FOREIGN KEY(task_id) REFERENCES runs(id) ON DELETE CASCADE
                );
                CREATE INDEX IF NOT EXISTS media_session ON media(session_id,created_at);
                CREATE INDEX IF NOT EXISTS media_message ON media(message_id);
                CREATE TABLE IF NOT EXISTS knowledge (
                    id TEXT PRIMARY KEY, name TEXT NOT NULL, chunks INTEGER NOT NULL, created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS chunks (
                    id INTEGER PRIMARY KEY, document_id TEXT NOT NULL, text TEXT NOT NULL,
                    embedding TEXT, embedding_model TEXT,
                    FOREIGN KEY(document_id) REFERENCES knowledge(id) ON DELETE CASCADE
                );
            ''')
            run_columns = {row['name'] for row in conn.execute('PRAGMA table_info(runs)')}
            if 'modeling_backend' not in run_columns:
                conn.execute("ALTER TABLE runs ADD COLUMN modeling_backend TEXT NOT NULL DEFAULT 'solidworks'")
            columns = {row['name'] for row in conn.execute('PRAGMA table_info(events)')}
            if 'task_id' not in columns:
                conn.execute('ALTER TABLE events ADD COLUMN task_id TEXT')
            conn.execute('CREATE INDEX IF NOT EXISTS events_run ON events(task_id,id)')

    @contextmanager
    def connect(self):
        with self.lock:
            conn = sqlite3.connect(self.path, timeout=15)
            conn.row_factory = sqlite3.Row
            conn.execute('PRAGMA foreign_keys=ON')
            try:
                yield conn
                conn.commit()
            except BaseException:
                conn.rollback()
                raise
            finally:
                conn.close()

    def sessions(self) -> list[dict]:
        with self.connect() as conn:
            return [dict(r) for r in conn.execute('SELECT id,title,updated_at FROM sessions ORDER BY updated_at DESC')]

    def session(self, session_id: str) -> dict | None:
        with self.connect() as conn:
            row = conn.execute('SELECT * FROM sessions WHERE id=?', (session_id,)).fetchone()
            return dict(row) if row else None

    def create_session(self, title: str = '新建对话') -> dict:
        item = {'id': uuid.uuid4().hex, 'title': title[:100], 'updated_at': now()}
        with self.connect() as conn:
            conn.execute('INSERT INTO sessions(id,title,updated_at) VALUES(:id,:title,:updated_at)', item)
        return item

    def delete_session(self, session_id: str):
        with self.connect() as conn:
            conn.execute('DELETE FROM sessions WHERE id=?', (session_id,))

    def messages(self, session_id: str, after: int = 0) -> list[dict]:
        with self.connect() as conn:
            result = [dict(r) for r in conn.execute(
                'SELECT * FROM messages WHERE session_id=? AND seq>? ORDER BY seq', (session_id, after))]
            media = conn.execute('SELECT id,session_id,message_id,task_id,kind,name,mime,width,height,size,created_at '
                                 'FROM media WHERE session_id=? AND message_id IS NOT NULL', (session_id,)).fetchall()
            grouped: dict = {}
            for item in media:
                grouped.setdefault(item['message_id'], []).append(dict(item))
            for message in result:
                message['attachments'] = grouped.get(message['id'], [])
            return result

    def add_message(self, session_id: str, role: str, content: str) -> dict:
        item = dict(id=uuid.uuid4().hex, session_id=session_id, role=role, content=content, created_at=now())
        with self.connect() as conn:
            if not conn.execute('SELECT 1 FROM sessions WHERE id=?', (session_id,)).fetchone():
                raise KeyError('Session not found')
            conn.execute('INSERT INTO messages(id,session_id,role,content,created_at) '
                         'VALUES(:id,:session_id,:role,:content,:created_at)', item)
            conn.execute('UPDATE sessions SET updated_at=? WHERE id=?', (item['created_at'], session_id))
            if role == 'user':
                conn.execute("UPDATE sessions SET title=? WHERE id=? AND title='新建对话'", (content[:45], session_id))
        return item

    def summarize(self, session_id: str, summary: str, through: int):
        with self.connect() as conn:
            conn.execute('UPDATE sessions SET summary=?,summary_through=? WHERE id=?', (summary, through, session_id))

    def event(self, session_id: str, event: dict):
        with self.connect() as conn:
            conn.execute('INSERT INTO events(session_id,payload,created_at,task_id) VALUES(?,?,?,?)',
                         (session_id, json.dumps(event, ensure_ascii=False), now(), event.get('task_id')))

    def events(self, session_id: str, limit: int | None = 100, task_id: str | None = None) -> list[dict]:
        with self.connect() as conn:
            query = 'SELECT id,payload,created_at FROM events WHERE session_id=?'
            arguments: list = [session_id]
            if task_id:
                query += ' AND task_id=?'
                arguments.append(task_id)
            query += ' ORDER BY id DESC'
            if limit is not None:
                query += ' LIMIT ?'
                arguments.append(limit)
            rows = conn.execute(query, arguments).fetchall()
            return [{'id': r['id'], 'event': json.loads(r['payload']), 'created_at': r['created_at']}
                    for r in reversed(rows)]

    def create_run(self, session_id: str, message: str, attachment_ids: list[str] | None = None,
                   *, modeling_backend: str = 'solidworks') -> dict:
        if modeling_backend not in {'solidworks', 'blender'}:
            raise ValueError('Invalid modeling backend')
        item = dict(id=uuid.uuid4().hex, session_id=session_id, owner_pid=os.getpid(),
                    status='running', message=message, detail='', created_at=now(), updated_at=now(), finished_at=None,
                    modeling_backend=modeling_backend)
        with self.connect() as conn:
            if not conn.execute('SELECT 1 FROM sessions WHERE id=?', (session_id,)).fetchone():
                raise KeyError('Session not found')
            ids = attachment_ids or []
            if len(ids) > 4 or len(ids) != len(set(ids)):
                raise ValueError('每轮最多附加 4 个不同的文件。')
            for image_id in ids:
                row = conn.execute('SELECT id,kind,message_id FROM media WHERE id=? AND session_id=?', (image_id, session_id)).fetchone()
                if not row or row['kind'] not in {'reference', 'document'} or row['message_id'] is not None:
                    raise ValueError('附件不存在、已用于其他消息或不属于当前会话。')
            message_id = uuid.uuid4().hex
            conn.execute('INSERT INTO messages(id,session_id,role,content,created_at) VALUES(?,?,?,?,?)',
                         (message_id, session_id, 'user', message, item['created_at']))
            conn.execute("UPDATE sessions SET updated_at=?,title=CASE WHEN title='新建对话' THEN ? ELSE title END WHERE id=?",
                         (item['created_at'], message[:45], session_id))
            conn.execute('INSERT INTO runs(id,session_id,owner_pid,status,message,detail,created_at,updated_at,finished_at,modeling_backend) '
                         'VALUES(:id,:session_id,:owner_pid,:status,:message,:detail,'
                         ':created_at,:updated_at,:finished_at,:modeling_backend)', item)
            for image_id in ids:
                conn.execute('UPDATE media SET message_id=?,task_id=? WHERE id=?', (message_id, item['id'], image_id))
        return item

    def update_run(self, task_id: str, status: str, detail: str = ''):
        if status not in {'running', 'waiting_confirmation', 'waiting_input', 'cancelling', 'completed', 'failed', 'cancelled', 'interrupted', 'max_tokens'}:
            raise ValueError('Invalid task status')
        finished = now() if status in {'completed', 'failed', 'cancelled', 'interrupted', 'max_tokens'} else None
        with self.connect() as conn:
            conn.execute('UPDATE runs SET status=?,detail=?,updated_at=?,finished_at=? WHERE id=?',
                         (status, detail[:2000], now(), finished, task_id))

    def run(self, task_id: str) -> dict | None:
        with self.connect() as conn:
            row = conn.execute('SELECT * FROM runs WHERE id=?', (task_id,)).fetchone()
            return dict(row) if row else None

    def runs(self, session_id: str, limit: int | None = 50) -> list[dict]:
        with self.connect() as conn:
            query = 'SELECT * FROM runs WHERE session_id=? ORDER BY created_at DESC'
            args: list = [session_id]
            if limit is not None:
                query += ' LIMIT ?'
                args.append(limit)
            return [dict(r) for r in conn.execute(query, args)]

    def recover_interrupted_runs(self):
        from .runtime import process_alive
        with self.connect() as conn:
            rows = conn.execute("SELECT id,owner_pid FROM runs WHERE status IN ('running','waiting_confirmation','waiting_input','cancelling')").fetchall()
            for row in rows:
                if not process_alive(row['owner_pid']):
                    stamp = now()
                    conn.execute("UPDATE runs SET status='interrupted',detail=?,updated_at=?,finished_at=? WHERE id=?",
                                 ('应用上次意外退出；请检查实际 SW 状态，已完成操作不会自动重放。', stamp, stamp, row['id']))

    def start_checkpoint(self, task_id: str, name: str, arguments: dict) -> int:
        stamp = now()
        with self.connect() as conn:
            cursor = conn.execute('INSERT INTO checkpoints(task_id,name,arguments,status,created_at,updated_at) '
                                  'VALUES(?,?,?,?,?,?)',
                                  (task_id, name, json.dumps(arguments, ensure_ascii=False), 'started', stamp, stamp))
            return cursor.lastrowid

    def finish_checkpoint(self, checkpoint_id: int, status: str, result: dict, snapshot: dict, warning: str = ''):
        if status not in {'returned', 'error', 'unknown'}:
            raise ValueError('Invalid checkpoint status')
        with self.connect() as conn:
            conn.execute('UPDATE checkpoints SET status=?,result=?,snapshot=?,warning=?,updated_at=? WHERE id=?',
                         (status, json.dumps(result, ensure_ascii=False), json.dumps(snapshot, ensure_ascii=False),
                          warning, now(), checkpoint_id))

    def checkpoints(self, task_id: str) -> list[dict]:
        with self.connect() as conn:
            rows = conn.execute('SELECT * FROM checkpoints WHERE task_id=? ORDER BY id', (task_id,)).fetchall()
            return [{**dict(row), **{key: json.loads(row[key]) for key in ('arguments', 'result', 'snapshot')}}
                    for row in rows]

    def knowledge(self) -> list[dict]:
        with self.connect() as conn:
            return [dict(r) for r in conn.execute('SELECT * FROM knowledge ORDER BY created_at DESC')]

    def add_document(self, name: str, chunks: list[str], vectors: list[list[float]] | None, model: str):
        item = dict(id=uuid.uuid4().hex, name=name, chunks=len(chunks), created_at=now())
        with self.connect() as conn:
            conn.execute('INSERT INTO knowledge VALUES(:id,:name,:chunks,:created_at)', item)
            conn.executemany('INSERT INTO chunks(document_id,text,embedding,embedding_model) VALUES(?,?,?,?)',
                             [(item['id'], text, json.dumps(vectors[i]) if vectors else None, model)
                              for i, text in enumerate(chunks)])
        return item

    def knowledge_page(self, query: str = '', offset: int = 0, limit: int = 20) -> dict:
        with self.connect() as conn:
            where = ' WHERE instr(lower(name), lower(?)) > 0' if query else ''
            args = [query] if query else []
            total = conn.execute('SELECT COUNT(*) FROM knowledge' + where, args).fetchone()[0]
            rows = conn.execute('SELECT * FROM knowledge' + where + ' ORDER BY created_at DESC,id LIMIT ? OFFSET ?',
                                [*args, limit, offset]).fetchall()
            end = offset + len(rows)
            return {'documents': [dict(r) for r in rows], 'total': total,
                    'next_offset': end if end < total else None}

    def document_page(self, document_id: str, offset: int = 0, limit: int = 5) -> dict:
        with self.connect() as conn:
            document = conn.execute('SELECT * FROM knowledge WHERE id=?', (document_id,)).fetchone()
            if not document:
                raise ValueError('知识库文档不存在或已删除，请先调用 knowledge_list 获取当前文档 ID。')
            rows = conn.execute('SELECT id,text FROM chunks WHERE document_id=? ORDER BY id LIMIT ? OFFSET ?',
                                (document_id, limit, offset)).fetchall()
            end = offset + len(rows)
            return {'document_id': document_id, 'name': document['name'], 'total_chunks': document['chunks'],
                    'offset': offset, 'next_offset': end if end < document['chunks'] else None,
                    'chunks': [{'chunk_id': row['id'], 'index': offset + i, 'text': row['text']}
                               for i, row in enumerate(rows)]}

    def chunks(self) -> list[dict]:
        with self.connect() as conn:
            return [dict(r) for r in conn.execute('SELECT c.*,k.name FROM chunks c JOIN knowledge k ON c.document_id=k.id')]

    def delete_document(self, document_id: str):
        with self.connect() as conn:
            conn.execute('DELETE FROM knowledge WHERE id=?', (document_id,))
