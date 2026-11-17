"""Numeric-only estimates for the actual outgoing chat request, never its contents."""
import json
import math


def _estimate(value):
    if not value:
        return 0
    wire = json.dumps(value, ensure_ascii=False, separators=(',', ':'))
    return math.ceil(len(wire.encode('utf-8')) / 4)


def context_usage(messages, tools):
    system, dialogue, images = [], [], 0
    for message in messages:
        # Internal error metadata is translated by native adapters, not sent as
        # a top-level Chat field. Keep estimates independent of this marker.
        message = {key: value for key, value in message.items() if key != 'is_error'}
        content = message.get('content')
        if isinstance(content, list):
            # Base64 transport bytes are not text tokens. Vision cost depends on the provider.
            parts = []
            for part in content:
                if part.get('type') == 'image_url':
                    images += 1
                else:
                    parts.append(part)
            message = {**message, 'content': parts}
        (system if message.get('role') == 'system' else dialogue).append(message)
    counts = {'system': _estimate(system), 'tools': _estimate(tools), 'messages': _estimate(dialogue)}
    return {**counts, 'total': sum(counts.values()), 'source': 'estimated', 'images': images}
