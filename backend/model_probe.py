"""Draft-only provider diagnostics. These calls never save settings or operate CAD."""
from __future__ import annotations

import anyio
import json
import secrets
import math
import time
from typing import Literal

import httpx
from openai import APIError, APIStatusError, AsyncOpenAI
from pydantic import Field, StrictBool

from .config import Settings
from .model_options import ModelOptions
from .model_capabilities import apply_capabilities
from .tool_protocol import has_tool_markup
from .model_endpoint import auth_headers, chat_endpoint, client_options, models_endpoint
from .model_transport import HISTORY_FIELD, ModelClient


class ModelDraft(ModelOptions):
    protocol: Literal['chat', 'responses', 'claude'] = 'chat'
    profile_id: str | None = Field(default=None, max_length=64)
    base_url: str = Field(max_length=2048)
    full_endpoint: StrictBool = False
    model: str = Field(default='', max_length=200)
    api_key: str = Field(default='', max_length=8192)


def draft_connection(settings, profiles, draft: ModelDraft | None):
    if draft is None:
        config, key, _ = profiles.resolve()
    else:
        config = Settings(**{**settings.value.model_dump(), 'base_url': draft.base_url,
                             'protocol': draft.protocol,
                             'full_endpoint': draft.full_endpoint, 'model': draft.model or 'model-list',
                             **draft.advanced_values()})
        key = draft.api_key.strip()
        if not key and draft.profile_id:
            saved_config, saved_key, _ = profiles.resolve(draft.profile_id)
            if saved_config.base_url != config.base_url or saved_config.full_endpoint != config.full_endpoint:
                raise ValueError('地址已修改，请重新填写此服务的 API Key 后测试。')
            key = saved_key
    if not key:
        raise ValueError('请填写当前模型服务的 API Key。')
    return apply_capabilities(config), key


async def list_models(config, key):
    url = models_endpoint(config)
    with anyio.fail_after(30):
        async with httpx.AsyncClient(timeout=25, follow_redirects=False) as client:
            headers = auth_headers(config, key)
            async with client.stream('GET', url, headers=headers) as response:
                response.raise_for_status()
                content = bytearray()
                async for chunk in response.aiter_bytes():
                    content.extend(chunk)
                    if len(content) > 2 * 1024 * 1024:
                        raise ValueError('模型列表响应过大，请手动填写模型 ID。')
                import json
                data = json.loads(content)
    if not isinstance(data, dict) or not isinstance(data.get('data'), list):
        raise ValueError('服务未返回有效的模型列表，请手动填写模型 ID。')
    models = sorted({item['id'] for item in data['data'] if isinstance(item, dict)
                     and isinstance(item.get('id'), str) and 0 < len(item['id'].strip()) <= 200})
    return {'models': models[:2000], 'models_url': url}


async def check_model(settings=None, *, config=None, key=None) -> dict:
    if config is None:
        config, key = settings.value, settings.key()
    if not key:
        raise ValueError('请先填写 API Key。')
    start = time.perf_counter()
    first = None
    output = ''
    tool_call = False
    calls = {}
    text_content = ''
    reasoning = {}
    provider_history = None
    protocol_error = ''
    roundtrip_verified = False
    finished = False
    incomplete = False
    completion_tokens = None
    usage_supported = True
    received_chars = 0
    with anyio.fail_after(40):
        async with ModelClient(config, key, chat_factory=AsyncOpenAI, chat_options=client_options, timeout=30) as client:
            options = dict(model=config.model,
                messages=[{'role': 'user', 'content': 'This is a connectivity test, not a CAD task. '
                           'Call connection_probe with value containing a short explanation (about 60 words) '
                           'of why engineering drawings specify dimensions and units.'}],
                tools=[{'type': 'function', 'function': {'name': 'connection_probe',
                        'description': 'A no-op connectivity test. Never executes an action.',
                        'parameters': {'type': 'object', 'properties': {'value': {'type': 'string'}},
                                       'required': ['value'], 'additionalProperties': False}}}],
                tool_choice='required', stream=True, max_tokens=config.max_output_tokens)
            for attempt in range(2):
                try:
                    extras = {'stream_options': {'include_usage': True}} if usage_supported else {}
                    response = await client.chat.completions.create(**options, **extras)
                    break
                except APIStatusError as exc:
                    if exc.status_code not in {400, 422}:
                        raise
                    message = str(exc).lower()
                    if 'tool_choice' in message:
                        raise ValueError('服务不支持本次测试要求的 tool_choice=required；工具协议验证未通过，请检查服务兼容性。') from exc
                    if attempt or not any(field in message for field in ('stream_options', 'include_usage')):
                        raise
                    usage_supported = False
            async with response:
                async for chunk in response:
                    usage = getattr(chunk, 'usage', None)
                    tokens = getattr(usage, 'completion_tokens', None)
                    if isinstance(tokens, int) and not isinstance(tokens, bool) and tokens > 0:
                        completion_tokens = tokens
                    for choice in chunk.choices:
                        delta = choice.delta
                        if getattr(delta, 'provider_history', None) is not None:
                            provider_history = delta.provider_history
                        for field in ('reasoning_content', 'reasoning'):
                            value = getattr(delta, field, None)
                            if isinstance(value, str):
                                received_chars += len(value)
                                reasoning[field] = reasoning.get(field, '') + value
                        fragment = delta.content or ''
                        text_content += fragment
                        for call in delta.tool_calls or []:
                            entry = calls.setdefault(call.index, {'id': '', 'type': 'function',
                                'function': {'name': '', 'arguments': ''}})
                            received_chars += len(call.id or '')
                            entry['id'] += call.id or ''
                            if call.function:
                                entry['function']['name'] += call.function.name or ''
                                entry['function']['arguments'] += call.function.arguments or ''
                                fragment += (call.function.name or '') + (call.function.arguments or '')
                        received_chars += len(fragment)
                        if received_chars > 64000 or len(calls) > 8:
                            raise ValueError('测试响应超出协议大小限制，已停止测试。')
                        if fragment:
                            if first is None:
                                first = time.perf_counter()
                            output += fragment
                        if choice.finish_reason in {'stop', 'tool_calls'}:
                            finished = True
                        if choice.finish_reason in {'length', 'content_filter'}:
                            incomplete = True
                        if len(output) > 32000:
                            raise ValueError('测试输出超出长度限制，已停止测试。')
            generation_elapsed = time.perf_counter() - start
            if first is None or not finished or incomplete:
                raise ValueError('流式响应为空、被截断或提前中断，未通过工具协议测试。')
            if len(calls) != 1:
                protocol_error = ('模型将工具调用输出为 DSML 文本。' if has_tool_markup(text_content)
                                  else '未收到唯一且完整的结构化工具调用。')
            else:
                candidate = next(iter(calls.values()))
                try:
                    args = json.loads(candidate['function']['arguments'])
                    tool_call = bool(candidate['id'] and candidate['function']['name'] == 'connection_probe'
                                     and isinstance(args, dict) and set(args) == {'value'}
                                     and isinstance(args['value'], str) and args['value'].strip())
                except (ValueError, TypeError):
                    tool_call = False
                if not tool_call:
                    protocol_error = '工具调用的 ID、名称或 JSON 参数不完整或不符合测试结构。'
            # No function is executed. A random receipt supplied only in the tool result
            # verifies that the provider can consume the second half of the protocol.
            if tool_call:
                receipt = secrets.token_hex(12)
                followup = options['messages'] + [
                    {'role': 'assistant', 'content': text_content or None, 'tool_calls': [candidate], **reasoning,
                     **({HISTORY_FIELD: provider_history} if provider_history is not None else {})},
                    {'role': 'tool', 'tool_call_id': candidate['id'],
                     'content': json.dumps({'receipt': receipt, 'instruction': 'Reply with exactly the receipt string. No tools.'})},
                ]
                reply = ''
                ended = False
                bad_finish = False
                unexpected_calls = False
                try:
                    stream = await client.chat.completions.create(model=config.model, messages=followup,
                        stream=True, max_tokens=config.max_output_tokens)
                    async with stream:
                        async for item in stream:
                            for choice in item.choices:
                                reply += choice.delta.content or ''
                                unexpected_calls = unexpected_calls or bool(choice.delta.tool_calls)
                                ended = ended or choice.finish_reason == 'stop'
                                bad_finish = bad_finish or choice.finish_reason in {'length', 'content_filter', 'tool_calls'}
                                if len(reply) > 32000:
                                    raise ValueError('工具结果回传测试输出超出长度限制。')
                    roundtrip_verified = ended and not bad_finish and not unexpected_calls and reply.strip() == receipt
                    if not roundtrip_verified:
                        protocol_error = '已收到合法工具调用，但工具结果回传验证未通过。'
                except (httpx.HTTPError, APIError, ValueError):
                    protocol_error = '已收到合法工具调用，但服务未完成工具结果回传验证。'
    elapsed = time.perf_counter() - start
    estimated = completion_tokens is None
    tokens = completion_tokens if not estimated else max(1, math.ceil(len(output.encode('utf-8')) / 4))
    return {'connected': True, 'tool_call_observed': tool_call,
            'tool_roundtrip_verified': roundtrip_verified,
            'detail': ('完整工具调用及结果回传验证通过；测试未操作 CAD。' if roundtrip_verified else
                       '流式连接成功，但工具协议测试未通过：' + protocol_error + ' 请检查模型服务的工具解析配置；连接成功不代表可执行建模。'),
            'elapsed_ms': round(elapsed * 1000), 'first_token_ms': round((first - start) * 1000),
            'output_tokens': tokens, 'tokens_per_second': round(tokens / max(generation_elapsed, 0.001), 2),
            'token_source': 'estimated' if estimated else 'usage', 'usage_supported': usage_supported,
            'endpoint': chat_endpoint(config)}
