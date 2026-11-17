"""Durable, task-scoped input consumed only between complete model/tool steps."""
from __future__ import annotations

from .storage import now


class SteeringStore:
    def __init__(self, db):
        self.db = db
        with db.connect() as conn:
            conn.executescript('''
                CREATE TABLE IF NOT EXISTS steering_inputs (
                    seq INTEGER PRIMARY KEY AUTOINCREMENT,
                    id TEXT NOT NULL, task_id TEXT NOT NULL, session_id TEXT NOT NULL,
                    content TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'pending',
                    created_at TEXT NOT NULL, message_id TEXT,
                    UNIQUE(session_id, id),
                    FOREIGN KEY(task_id) REFERENCES runs(id) ON DELETE CASCADE,
                    FOREIGN KEY(session_id) REFERENCES sessions(id) ON DELETE CASCADE
                );
            ''')

    def entries(self, session_id):
        with self.db.connect() as conn:
            rows = conn.execute('''SELECT s.*, r.status AS task_status FROM steering_inputs s
                JOIN runs r ON r.id=s.task_id WHERE s.session_id=? ORDER BY s.seq''', (session_id,))
            return [{**dict(row), 'status': 'released' if row['status'] == 'pending' and
                     row['task_status'] not in {'running', 'waiting_input', 'waiting_confirmation'}
                     else row['status']} for row in rows]

    def find(self, session_id, item_id):
        return next((row for row in self.entries(session_id) if row['id'] == item_id), None)


class SteeringInbox:
    def __init__(self, store, session_id, task_id):
        self.store, self.session_id, self.task_id = store, session_id, task_id
        self.open = True

    def pending(self):
        return [row for row in self.store.entries(self.session_id)
                if row['task_id'] == self.task_id and row['status'] == 'pending']

    def accept(self, item_id, content):
        existing = self.store.find(self.session_id, item_id)
        if existing:
            if existing['task_id'] != self.task_id or existing['content'] != content:
                raise ValueError('插话标识已用于另一条消息。')
            return existing
        if not self.open:
            raise ValueError('当前任务已结束接收插话，消息仍保留在队列中。')
        with self.store.db.connect() as conn:
            count = conn.execute('SELECT COUNT(*) FROM steering_inputs WHERE task_id=?', (self.task_id,)).fetchone()[0]
            if count >= 30:
                raise ValueError('本轮最多接收 30 条插话，其余消息请留待下一轮。')
            conn.execute('INSERT INTO steering_inputs(id,task_id,session_id,content,created_at) VALUES(?,?,?,?,?)',
                         (item_id, self.task_id, self.session_id, content, now()))
        return self.store.find(self.session_id, item_id)

    def claim(self):
        # Persist admission and the user message together, before making a model request.
        messages = []
        with self.store.db.connect() as conn:
            rows = conn.execute("SELECT * FROM steering_inputs WHERE task_id=? AND status='pending' ORDER BY seq",
                                (self.task_id,)).fetchall()
            for row in rows:
                message_id = 'steer-' + str(row['seq'])
                stamp = now()
                cursor = conn.execute('INSERT INTO messages(id,session_id,role,content,created_at) VALUES(?,?,?,?,?)',
                                      (message_id, self.session_id, 'user', row['content'], stamp))
                conn.execute("UPDATE steering_inputs SET status='consumed',message_id=? WHERE seq=?",
                             (message_id, row['seq']))
                conn.execute('UPDATE sessions SET updated_at=? WHERE id=?', (stamp, self.session_id))
                messages.append({'id': message_id, 'seq': cursor.lastrowid, 'role': 'user',
                                 'content': row['content'], 'created_at': stamp, 'queue_id': row['id']})
        return messages

    def close(self):
        self.open = False

    def release(self):
        self.close()
        with self.store.db.connect() as conn:
            conn.execute("UPDATE steering_inputs SET status='released' WHERE task_id=? AND status='pending'",
                         (self.task_id,))
