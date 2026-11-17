"""Context-window budgets and atomic conversation compaction."""
from __future__ import annotations

import json
from collections.abc import Awaitable, Callable

from .model_retry import RetryPolicy, retry_model_request

from .context_usage import context_usage
from .prompting import SUMMARY_PROMPT, request_messages
from .tool_protocol import assistant_history_text


def input_threshold(context_window: int, max_output_tokens: int) -> int:
    return min(context_window * 4 // 5, context_window - max_output_tokens)


def request_size(messages: list[dict], tools: list[dict], instructions: str = '') -> int:
    outgoing = request_messages(messages, instructions) if instructions else messages
    usage = context_usage(outgoing, tools)
    # Image token costs vary by provider; reserve an estimate separately from base64 bytes.
    return usage['total'] + usage['images'] * 1024


def compression_plan(messages: list[dict], tools: list[dict], threshold: int,
                     instructions: str, *, history_only: bool = False,
                     protected_user_index: int | None = None,
                     group_ids: dict[int, int] | None = None) -> tuple[list[int], list[dict]]:
    """Remove old complete exchanges, pinning the latest user request and system rules."""
    latest_user = (protected_user_index if protected_user_index is not None else
                   max((i for i, m in enumerate(messages) if m['role'] == 'user'), default=-1))
    pending: set[str] = set()
    boundaries = []
    for i, message in enumerate(messages):
        pending.update(c['id'] for c in message.get('tool_calls', []))
        if message['role'] == 'tool':
            pending.discard(message.get('tool_call_id'))
        if not pending:
            boundaries.append(i + 1)
    if pending:
        raise ValueError('Cannot compress an incomplete tool exchange.')
    # Always retain the final exchange, including all calls and results in a batch.
    last_start = boundaries[-2] if len(boundaries) > 1 else 0
    last_call = max((i for i, m in enumerate(messages) if m.get('tool_calls')), default=-1)
    if last_call >= 0:
        last_start = min(last_start, last_call)
    best = ([], messages)
    for cut in boundaries:
        if cut > last_start or (history_only and latest_user >= 0 and cut > latest_user):
            break
        if group_ids:
            before = {group for index, group in group_ids.items() if index < cut}
            after = {group for index, group in group_ids.items() if index >= cut}
            if before & after:
                continue
        removed = [i for i in range(cut) if i != latest_user and messages[i]['role'] != 'system']
        if not removed:
            continue
        removed_set = set(removed)
        retained = [m for i, m in enumerate(messages) if i not in removed_set]
        best = (removed, retained)
        if request_size(retained, tools, instructions) <= threshold * 3 // 5:
            break
    return best


def summary_transcript(messages: list[dict]) -> str:
    records = []
    for message in messages:
        record = {k: v for k, v in message.items() if k != '_provider_history'}
        content = record.get('content')
        if isinstance(content, list):
            record['content'] = [
                {'type': 'text', 'text': '[Image omitted from summary; recheck visual evidence.]'}
                if part.get('type') == 'image_url' else part for part in content
            ]
        records.append(record)
    return json.dumps(records, ensure_ascii=False)


async def summarize_messages(client, *, model: str, messages: list[dict],
                             previous_summary: str, context_window: int,
                             max_output_tokens: int,
                             on_retry: Callable[[int, int], Awaitable[None]] | None = None,
                             retry_policy: RetryPolicy | None = None) -> str:
    """Summarize bounded blocks without silently dropping older transcript text."""
    transcript = summary_transcript(messages)
    summary = previous_summary
    output_limit = min(max_output_tokens, 2048, max(1, context_window // 8))
    input_limit = input_threshold(context_window, output_limit)
    offset = 0
    while offset < len(transcript):
        def payload(length: int) -> list[dict]:
            return [
                {'role': 'system', 'content': SUMMARY_PROMPT},
                {'role': 'user', 'content': json.dumps({
                    'previous_summary': summary,
                    'older_messages': transcript[offset:offset + length],
                }, ensure_ascii=False)},
            ]

        low, high = 0, min(24000, len(transcript) - offset)
        while low < high:
            middle = (low + high + 1) // 2
            if request_size(payload(middle), []) < input_limit:
                low = middle
            else:
                high = middle - 1
        if not low:
            raise ValueError('Context window is too small for the summary request and output reserve.')
        async def create_summary():
            return await client.chat.completions.create(
                model=model, max_tokens=output_limit, messages=payload(low))

        async def notify_retry(attempt: int, delay: int):
            if on_retry:
                await on_retry(attempt, delay)

        response = await retry_model_request(create_summary, notify_retry, policy=retry_policy)
        choice = response.choices[0]
        if getattr(choice, 'finish_reason', None) not in {None, 'stop'}:
            raise ValueError('Context summary was interrupted; original history is retained.')
        content = choice.message.content
        if not isinstance(content, str) or not content.strip() or getattr(choice.message, 'tool_calls', None):
            raise ValueError('Context summary was empty or contained tool calls; original history is retained.')
        summary = assistant_history_text(content)
        offset += low
    return summary


async def compact_context(client, *, model: str, messages: list[dict], tools: list[dict],
                          context_window: int, max_output_tokens: int, instructions: str,
                          previous_summary: str, system_builder: Callable[[str], str],
                          history_only: bool = False, protected_user_index: int | None = None,
                          group_ids: dict[int, int] | None = None,
                          on_retry: Callable[[int, int], Awaitable[None]] | None = None,
                          retry_policy: RetryPolicy | None = None, force: bool = False
                          ) -> tuple[list[dict], str, list[int]]:
    threshold = input_threshold(context_window, max_output_tokens)
    size = request_size(messages, tools, instructions)
    if force:
        threshold = min(threshold, max(1, size * 3 // 4))
    if not force and size < threshold:
        return messages, previous_summary, []
    removed, retained = compression_plan(
        messages, tools, threshold, instructions, history_only=history_only,
        protected_user_index=protected_user_index, group_ids=group_ids)
    if not removed:
        if request_size(messages, tools, instructions) >= context_window - max_output_tokens:
            raise ValueError('The current request exceeds the input budget; narrow the request or increase the configured context window.')
        return messages, previous_summary, []
    summary = await summarize_messages(
        client, model=model, messages=[messages[i] for i in removed],
        previous_summary=previous_summary, context_window=context_window,
        max_output_tokens=max_output_tokens, on_retry=on_retry, retry_policy=retry_policy)
    compacted = [{**retained[0], 'content': system_builder(summary)}, *retained[1:]]
    if request_size(compacted, tools, instructions) >= context_window - max_output_tokens:
        raise ValueError('Context remains over budget after compression; original history is retained.')
    if request_size(compacted, tools, instructions) >= request_size(messages, tools, instructions):
        raise ValueError('The summary did not reduce context size; original history is retained.')
    return compacted, summary, removed
