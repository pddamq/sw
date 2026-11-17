"""Validate complete tool exchanges before serializing any provider request."""
from __future__ import annotations

import json

from .output_guard import strict_arguments

HISTORY_FIELD = '_provider_history'


class HistoryValidationError(ValueError):
    """Local history corruption, not a retryable provider or CAD failure."""


def validate_history(messages, protocol, model):
    pending, used = set(), set()
    for message in messages:
        role = message.get('role')
        calls = message.get('tool_calls') or []
        if not isinstance(calls, list) or (calls and role != 'assistant'):
            raise HistoryValidationError('tool_history: calls must be an assistant list.')
        if role == 'tool':
            call_id = message.get('tool_call_id')
            if not isinstance(call_id, str) or call_id not in pending:
                raise HistoryValidationError('tool_history: result has no pending matching call.')
            pending.remove(call_id)
            continue
        if pending:
            raise HistoryValidationError('tool_history: tool calls require immediate complete results.')
        if calls and role != 'assistant':
            raise HistoryValidationError('tool_history: calls must belong to an assistant message.')
        canonical = []
        for call in calls:
            try:
                call_id, function = call['id'], call['function']
                if (not isinstance(call_id, str) or not call_id.strip() or call_id in used
                        or not isinstance(function['name'], str) or not function['name']):
                    raise ValueError('Invalid identity')
                canonical.append((call_id, function['name'], strict_arguments(function['arguments'])))
            except (KeyError, TypeError, ValueError) as exc:
                raise HistoryValidationError('tool_history: invalid or duplicate historical call.') from exc
            used.add(call_id)
            pending.add(call_id)
        replay = message.get(HISTORY_FIELD)
        if (role != 'assistant' or not isinstance(replay, dict)
                or replay.get('protocol') != protocol or replay.get('model') != model):
            continue
        items = replay.get('items')
        if not isinstance(items, list):
            raise HistoryValidationError('tool_history: native replay items must be a list.')
        native = []
        try:
            for item in items:
                kind = item['type']
                allowed = ({'text', 'thinking', 'redacted_thinking', 'tool_use'} if protocol == 'claude'
                           else {'message', 'reasoning', 'function_call'})
                if kind not in allowed:
                    raise ValueError('Unsupported replay item')
                if kind == 'tool_use':
                    if not isinstance(item['input'], dict):
                        raise ValueError('Invalid input')
                    native.append((item['id'], item['name'], item['input']))
                elif kind == 'function_call':
                    native.append((item['call_id'], item['name'], strict_arguments(item['arguments'])))
        except (KeyError, TypeError, ValueError) as exc:
            raise HistoryValidationError('tool_history: malformed native replay.') from exc
        if json.dumps(native, sort_keys=True, allow_nan=False) != json.dumps(canonical, sort_keys=True, allow_nan=False):
            raise HistoryValidationError('tool_history: native replay disagrees with validated calls.')
        text = ''.join(item.get('text', '') for item in items if item['type'] == 'text') if protocol == 'claude' else ''.join(
            part.get('text', part.get('refusal', '')) for item in items if item['type'] == 'message'
            for part in item.get('content', []) if part.get('type') in {'output_text', 'refusal'})
        if text != (message.get('content') or ''):
            raise HistoryValidationError('tool_history: native replay disagrees with validated text.')
    if pending:
        raise HistoryValidationError('tool_history: unresolved calls at end of request.')
