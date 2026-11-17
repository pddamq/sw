"""Generation-attempt identity and observable phases without unvalidated text."""
import time
import uuid
from datetime import datetime, timezone


class GenerationAttempts:
    def __init__(self, emit):
        self.emit = emit
        self.id = None
        self.revision = 0
        self.round = 0
        self.number = 0
        self.started = 0.0
        self.timestamp = ''
        self.phase = ''

    async def start(self, round, number):
        self.id = uuid.uuid4().hex
        self.round, self.number = round, number
        self.started = time.monotonic()
        self.timestamp = datetime.now(timezone.utc).isoformat()
        await self.update('waiting')

    async def update(self, phase, **extra):
        if not self.id:
            return
        self.phase = phase
        self.revision += 1
        await self.emit({'type': 'model_attempt', 'attempt_id': self.id, 'revision': self.revision,
                         'round': self.round, 'attempt': self.number, 'phase': phase,
                         'started_at': self.timestamp,
                         'elapsed_ms': round((time.monotonic() - self.started) * 1000), **extra})

    async def receiving(self):
        if self.phase == 'waiting':
            await self.update('receiving')
