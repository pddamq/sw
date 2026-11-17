"""Bounded status projection and paging for the executor's append-only operation log.

``sw_status.recentOps`` is deliberately bounded (the upstream executor keeps the
last 1000 operations).  A long task can exceed that window, so the application
must not pretend the truncated list is the complete history.  The executor also
writes every completed operation to ``operations.jsonl``; this module exposes a
small read-only tool that pages that log newest-first, similar to dsh's spill
read path, while keeping the model-facing status compact.
"""
from __future__ import annotations

import json
from pathlib import Path

from .config import DATA_DIR

MAX_HISTORY_BYTES = 8 * 1024 * 1024
STATUS_HEAD = 5
STATUS_TAIL = 45
OP_KEYS = ('name', 'startedUtc', 'elapsedS', 'status', 'error')

HISTORY_TOOL = {'type': 'function', 'function': {
    'name': 'executor_history',
    'description': ('Read the executor append-only operation log newest-first. '
                    'Use it when sw_status reports recentOpsTruncated, or when you need '
                    'operations older than the bounded recentOps window. Returns total, '
                    'matched, offset, limit, next_offset and entries; this is read-only '
                    'and does not touch SOLIDWORKS.'),
    'parameters': {'type': 'object', 'properties': {
        'offset': {'type': 'integer', 'minimum': 0, 'maximum': 10_000_000,
                   'description': 'Skip this many newest matching entries. Default 0.'},
        'limit': {'type': 'integer', 'minimum': 1, 'maximum': 200,
                  'description': 'Maximum entries to return. Default 50, maximum 200.'},
        'name': {'type': 'string', 'maxLength': 120,
                 'description': 'Optional case-insensitive tool-name filter, for example model_info.'},
    }, 'additionalProperties': False}}}


def operation_log_path() -> Path:
    return Path(DATA_DIR) / 'executor' / 'operations.jsonl'


def _compact_operation(value) -> dict:
    if not isinstance(value, dict):
        return {'value': value}
    compact = {key: value[key] for key in OP_KEYS if key in value}
    # Keep an unexpected upstream field rather than silently hiding evidence.
    for key, item in value.items():
        if key not in compact:
            compact[key] = item
    return compact


def project_status(data: dict) -> dict:
    """Return a compact, self-describing status for the model and the UI."""
    if not isinstance(data, dict):
        return data
    projected = {key: value for key, value in data.items() if key != 'recentOps'}
    operations = data.get('recentOps')
    total = data.get('recentOpsTotal')
    if not isinstance(total, int) or isinstance(total, bool):
        total = len(operations) if isinstance(operations, list) else 0
    kept = []
    omitted = 0
    if isinstance(operations, list) and operations:
        if len(operations) <= STATUS_HEAD + STATUS_TAIL:
            kept = [_compact_operation(item) for item in operations]
        else:
            head = operations[:STATUS_HEAD]
            tail = operations[-STATUS_TAIL:]
            kept = [_compact_operation(item) for item in head + tail]
            omitted = len(operations) - len(head) - len(tail)
    projected['recentOps'] = kept
    projected['recentOpsTotal'] = total
    projected['recentOpsLimit'] = data.get('recentOpsLimit')
    projected['recentOpsTruncated'] = bool(data.get('recentOpsTruncated')) or omitted > 0
    projected['recentOpsWindow'] = {
        'keptHead': min(STATUS_HEAD, len(kept)),
        'keptTail': max(0, len(kept) - min(STATUS_HEAD, len(kept))),
        'omitted': omitted,
        'note': ('recentOps is a bounded window, not the complete task history. '
                 'Use executor_history(offset, limit) to page the append-only log.'),
    }
    log = data.get('operationLog')
    projected['operationLog'] = log or str(operation_log_path())
    if data.get('vbaEnabled') is False and not data.get('vbaNote'):
        projected['vbaNote'] = (
            'vba_run is disabled because this executor process was started without '
            'SWMCP_VBA=on. Enable VBA in the executor settings and reconnect; until '
            'then vba_run is not advertised and no macro can run.'
        )
    projected['retention'] = {
        'source': 'sw_status.recentOps',
        'fullHistoryTool': 'executor_history',
        'operationLog': projected['operationLog'],
        'recentOpsOmitted': omitted,
    }
    return projected


def project_status_text(result: dict) -> str:
    """Project a raw ``sw_status`` tool result without changing checkpoints or data."""
    content = result.get('content', '')
    try:
        data = json.loads(content)
    except (ValueError, TypeError):
        return content
    if not isinstance(data, dict):
        return content
    return json.dumps(project_status(data), ensure_ascii=False)


def _read_log(path: Path) -> tuple[list, bool]:
    if not path.is_file():
        return [], False
    partial = False
    with path.open('rb') as handle:
        handle.seek(0, 2)
        size = handle.tell()
        start = max(0, size - MAX_HISTORY_BYTES)
        handle.seek(start)
        raw = handle.read()
    if start > 0:
        partial = True
        # Drop the first partial line so malformed JSON is not reported as an operation.
        newline = raw.find(b'\n')
        raw = raw[newline + 1:] if newline >= 0 else b''
    rows = []
    for line in raw.splitlines():
        try:
            value = json.loads(line.decode('utf-8', errors='replace'))
        except (ValueError, TypeError):
            continue
        if isinstance(value, dict):
            rows.append(value)
    return rows, partial


def read_history(offset: int = 0, limit: int = 50, name: str | None = None, *,
                 path: str | Path | None = None) -> dict:
    """Page the operation log newest-first; ``offset`` skips newest matches."""
    offset = max(0, int(offset or 0))
    limit = min(200, max(1, int(limit or 50)))
    target = Path(path) if path else operation_log_path()
    rows, partial = _read_log(target)
    if not rows and not target.is_file():
        return {'path': str(target), 'total': 0, 'matched': 0, 'offset': offset,
                'limit': limit, 'entries': [], 'next_offset': None, 'partial': False,
                'note': 'No executor operation log exists yet. sw_status remains the live source; '
                        'this log appears after the executor completes its first operation.'}
    total = len(rows)
    if name:
        needle = str(name).strip().casefold()
        rows = [row for row in rows if needle in str(row.get('name', '')).casefold()]
    rows.reverse()
    entries = rows[offset:offset + limit]
    next_offset = offset + len(entries)
    return {'path': str(target), 'total': total, 'matched': len(rows),
            'offset': offset, 'limit': limit, 'entries': entries,
            'next_offset': next_offset if next_offset < len(rows) else None,
            'partial': partial,
            'note': ('Newest first. The log is append-only and survives executor restarts; '
                     'sw_status.recentOps is only a bounded window. '
                     + ('Only the tail of an oversized log was read.' if partial else ''))}
