"""Native Responses/Messages transports normalized to the agent's internal turn format.

The chat-shaped interface is internal only. Non-chat services never send requests
through Chat Completions, including diagnostics and context summarization.
"""
from __future__ import annotations

import copy
import json
from types import SimpleNamespace as NS

import httpx
from openai import APIStatusError

from .model_endpoint import auth_headers, chat_endpoint
from .model_history import validate_history

HISTORY_FIELD = '_provider_history'
MAX_EVENT_BYTES = 2 * 1024 * 1024
MAX_RESPONSE_BYTES = 32 * 1024 * 1024


def chunk(*, content=None, calls=None, reasoning=None, finish=None, usage=None, history=None):
    delta = NS(content=content, tool_calls=calls or [], reasoning_content=reasoning,
               provider_history=history)
    return NS(choices=[NS(index=0, delta=delta, finish_reason=finish)], usage=usage)


def tool_chunk(index, id, name, arguments):
    return NS(index=index, id=id, type='function', function=NS(name=name, arguments=arguments))


def usage_value(value, protocol='responses'):
    prompt = value.get('input_tokens')
    if protocol == 'claude' and type(prompt) is int and prompt >= 0:
        # Messages reports cache reads/writes separately; Responses includes them.
        cache = [value.get(key, 0) for key in ('cache_read_input_tokens', 'cache_creation_input_tokens')]
        prompt = prompt + sum(cache) if all(type(n) is int and n >= 0 for n in cache) else None
    details = value.get('output_tokens_details') or {}
    return NS(prompt_tokens=prompt, completion_tokens=value.get('output_tokens'),
              completion_tokens_details=NS(reasoning_tokens=details.get('reasoning_tokens')))


def native_history(message, protocol, model):
    history = message.get(HISTORY_FIELD)
    if isinstance(history, dict) and history.get('protocol') == protocol and history.get('model') == model:
        return copy.deepcopy(history.get('items', []))
    return None


def text_parts(content, *, protocol, assistant=False):
    if isinstance(content, str):
        return [{'type': 'text' if protocol == 'claude' else ('output_text' if assistant else 'input_text'), 'text': content}] if content else []
    parts = []
    for part in content or []:
        if part.get('type') == 'text':
            parts.extend(text_parts(part['text'], protocol=protocol, assistant=assistant))
        elif part.get('type') == 'image_url':
            url = part['image_url']['url']
            if protocol == 'responses':
                parts.append({'type': 'input_image', 'image_url': url,
                              'detail': part['image_url'].get('detail', 'auto')})
            else:
                if url.startswith('data:'):
                    header, data = url.split(',', 1)
                    if not header.endswith(';base64'):
                        raise ValueError('Messages images require base64 data URLs.')
                    source = {'type': 'base64', 'media_type': header[5:-7], 'data': data}
                else:
                    source = {'type': 'url', 'url': url}
                parts.append({'type': 'image', 'source': source})
        else:
            raise ValueError('Unsupported model content block: ' + str(part.get('type')))
    return parts


def responses_request(options):
    validate_history(options['messages'], 'responses', options['model'])
    inputs = []
    for message in options['messages']:
        role = message['role']
        replay = native_history(message, 'responses', options['model']) if role == 'assistant' else None
        if replay is not None:
            inputs.extend(replay)
            continue
        if role == 'tool':
            inputs.append({'type': 'function_call_output', 'call_id': message['tool_call_id'],
                           'output': message.get('content') or ''})
            continue
        content = text_parts(message.get('content'), protocol='responses', assistant=role == 'assistant')
        if content:
            inputs.append({'role': role, 'content': content})
        for call in message.get('tool_calls', []):
            inputs.append({'type': 'function_call', 'call_id': call['id'],
                           'name': call['function']['name'], 'arguments': call['function']['arguments']})
    payload = {'model': options['model'], 'input': inputs, 'stream': bool(options.get('stream')),
               'max_output_tokens': options['max_tokens'], 'store': False,
               'include': ['reasoning.encrypted_content']}
    if 'temperature' in options and not options.get('reasoning_effort'):
        payload['temperature'] = options['temperature']
    if options.get('reasoning_effort'):
        payload['reasoning'] = {'effort': options['reasoning_effort']}
    if options.get('tools'):
        payload['tools'] = [{'type': 'function', 'strict': False, **tool['function']} for tool in options['tools']]
        choice = options.get('tool_choice', 'auto')
        payload['tool_choice'] = ({'type': 'function', 'name': choice['function']['name']}
                                  if isinstance(choice, dict) else choice)
    return payload


def claude_request(options):
    validate_history(options['messages'], 'claude', options['model'])
    system, messages = [], []
    for message in options['messages']:
        role = message['role']
        if role in {'system', 'developer'}:
            system.extend(text_parts(message.get('content'), protocol='claude'))
            continue
        if role == 'tool':
            role = 'user'
            blocks = [{'type': 'tool_result', 'tool_use_id': message['tool_call_id'],
                       'content': message.get('content') or '',
                       **({'is_error': True} if message.get('is_error') else {})}]
        else:
            replay = native_history(message, 'claude', options['model']) if role == 'assistant' else None
            blocks = replay if replay is not None else text_parts(message.get('content'), protocol='claude')
            if replay is None:
                for call in message.get('tool_calls', []):
                    blocks.append({'type': 'tool_use', 'id': call['id'], 'name': call['function']['name'],
                                   'input': json.loads(call['function']['arguments'])})
        if not blocks:
            continue
        if messages and messages[-1]['role'] == role:
            messages[-1]['content'].extend(blocks)
        else:
            messages.append({'role': role, 'content': blocks})
    payload = {'model': options['model'], 'messages': messages,
               'max_tokens': options['max_tokens'], 'stream': bool(options.get('stream'))}
    if system:
        payload['system'] = system
    if 'temperature' in options:
        payload['temperature'] = options['temperature']
    if options.get('reasoning_effort'):
        # Effort support is model-specific; use the Messages adaptive-thinking contract.
        payload.pop('temperature', None)
        payload['thinking'] = {'type': 'adaptive'}
        payload['output_config'] = {'effort': options['reasoning_effort']}
    if options.get('tools'):
        payload['tools'] = [{'name': tool['function']['name'],
                             'description': tool['function'].get('description', ''),
                             'input_schema': tool['function']['parameters']} for tool in options['tools']]
        choice = options.get('tool_choice', 'auto')
        # Messages thinking rejects forced tools. Repair remains mandatory in
        # the agent's response validator rather than in this wire option.
        if options.get('reasoning_effort') and (choice == 'required' or isinstance(choice, dict)):
            choice = 'auto'
        payload['tool_choice'] = ({'type': 'tool', 'name': choice['function']['name']}
                                  if isinstance(choice, dict) else {'type': 'any' if choice == 'required' else choice})
    return payload


def stream_error(response, event):
    error = event.get('error') or event.get('response', {}).get('error') or {}
    kind = error.get('type') or error.get('code') or 'provider_error'
    status = {'overloaded_error': 529, 'rate_limit_error': 429, 'server_error': 500,
              'api_error': 500}.get(kind, 400)
    synthetic = httpx.Response(status, request=response.request, headers=response.headers)
    raise APIStatusError(str(error.get('message') or kind), response=synthetic, body=event)


async def events(response):
    data, size, total = [], 0, 0
    async for line in response.aiter_lines():
        total += len(line.encode('utf-8'))
        size += len(line.encode('utf-8'))
        if size > MAX_EVENT_BYTES or total > MAX_RESPONSE_BYTES:
            raise ValueError('Model stream exceeded the response size limit.')
        if not line:
            if data:
                raw = '\n'.join(data)
                if raw == '[DONE]':
                    return
                value = json.loads(raw)
                if not isinstance(value, dict):
                    raise ValueError('Model stream event must be an object.')
                yield value
            data, size = [], 0
        elif line.startswith('data:'):
            data.append(line[5:].lstrip(' '))
    # An unterminated SSE frame is incomplete, never a successful finish marker.


class NativeStream:
    def __init__(self, response, protocol, model):
        self.response, self.protocol, self.model = response, protocol, model

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        await self.response.aclose()

    def __aiter__(self):
        return self.translate()

    async def translate(self):
        blocks, partial_json, sent_calls = {}, {}, set()
        closed_blocks = set()
        response_calls = {}
        finish, usage = None, {}
        replay_valid = True

        def response_call(item, *, final=False):
            id = item.get('call_id', '')
            if id not in response_calls:
                response_calls[id] = {'index': len(response_calls), 'name': item.get('name', ''), 'arguments': ''}
                initial = tool_chunk(response_calls[id]['index'], id, item.get('name', ''), '')
            else:
                initial = None
            state = response_calls[id]
            arguments = item.get('arguments', '')
            if state['name'] != item.get('name', '') or not arguments.startswith(state['arguments']):
                raise ValueError('Responses tool call changed during generation.')
            tail = arguments[len(state['arguments']):]
            state['arguments'] = arguments
            if final:
                sent_calls.add(id)
            return [part for part in (initial, tool_chunk(state['index'], '', '', tail) if tail else None) if part]

        async for event in events(self.response):
            kind = event.get('type')
            if kind in {'error', 'response.failed'}:
                stream_error(self.response, event)
            if self.protocol == 'responses':
                if kind == 'response.output_text.delta':
                    yield chunk(content=event.get('delta', ''))
                elif kind == 'response.refusal.delta':
                    yield chunk(content=event.get('delta', ''))
                elif kind == 'response.reasoning_summary_text.delta':
                    yield chunk(reasoning=event.get('delta', ''))
                elif kind == 'response.output_item.added' and event['item'].get('type') == 'function_call':
                    item = event['item']
                    blocks[event['output_index']] = item
                    yield chunk(calls=response_call(item))
                elif kind == 'response.function_call_arguments.delta':
                    item = blocks[event['output_index']]
                    item['arguments'] = item.get('arguments', '') + event.get('delta', '')
                    yield chunk(calls=response_call(item))
                elif kind == 'response.output_item.done':
                    item = event['item']
                    if item.get('type') == 'function_call':
                        if item.get('call_id', '') in sent_calls:
                            raise ValueError('Duplicate Responses tool call ID.')
                        yield chunk(calls=response_call(item, final=True))
                elif kind in {'response.completed', 'response.incomplete'}:
                    result = event['response']
                    items = result.get('output', [])
                    final_ids = set()
                    for item in items:
                        if item.get('type') not in {'message', 'reasoning', 'function_call'}:
                            raise ValueError('Unsupported Responses output item.')
                        if item.get('type') == 'function_call':
                            id = item.get('call_id', '')
                            if id in final_ids:
                                raise ValueError('Duplicate Responses tool call ID.')
                            final_ids.add(id)
                            yield chunk(calls=response_call(item, final=True))
                    if set(response_calls) != final_ids:
                        raise ValueError('Responses final tool calls do not match the stream.')
                    if kind == 'response.incomplete' or result.get('status') != 'completed':
                        reason = (result.get('incomplete_details') or {}).get('reason')
                        finish = {'max_output_tokens': 'length', 'content_filter': 'content_filter'}.get(reason, 'incomplete')
                    else:
                        finish = 'tool_calls' if sent_calls else 'stop'
                    yield chunk(finish=finish, usage=usage_value(result.get('usage') or {}),
                                history={'protocol': self.protocol, 'model': self.model, 'items': items})
                    return
            else:
                if kind == 'message_start':
                    usage.update(event.get('message', {}).get('usage') or {})
                    yield chunk(usage=usage_value(usage, self.protocol))
                elif kind == 'content_block_start':
                    index, block = event['index'], copy.deepcopy(event['content_block'])
                    if index in blocks:
                        raise ValueError('Duplicate Messages content block index.')
                    if block.get('type') not in {'text', 'thinking', 'redacted_thinking', 'tool_use'}:
                        raise ValueError('Unsupported Messages content block.')
                    blocks[index] = block
                    if block['type'] == 'text' and block.get('text'):
                        yield chunk(content=block['text'])
                    elif block['type'] == 'thinking' and block.get('thinking'):
                        yield chunk(reasoning=block['thinking'])
                    elif block['type'] == 'tool_use':
                        id = block.get('id', '')
                        if id in sent_calls:
                            raise ValueError('Duplicate Messages tool call ID.')
                        response_calls[index] = len(sent_calls)
                        sent_calls.add(id)
                        yield chunk(calls=[tool_chunk(response_calls[index], id, block.get('name', ''), '')])
                elif kind == 'content_block_delta':
                    index, delta = event['index'], event['delta']
                    if index in closed_blocks:
                        raise ValueError('Messages content arrived after its block stopped.')
                    block = blocks[index]
                    if delta['type'] == 'text_delta':
                        block['text'] = block.get('text', '') + delta['text']
                        yield chunk(content=delta['text'])
                    elif delta['type'] == 'thinking_delta':
                        block['thinking'] = block.get('thinking', '') + delta['thinking']
                        yield chunk(reasoning=delta['thinking'])
                    elif delta['type'] == 'signature_delta':
                        block['signature'] = block.get('signature', '') + delta['signature']
                    elif delta['type'] == 'input_json_delta':
                        partial_json[index] = partial_json.get(index, '') + delta['partial_json']
                        yield chunk(calls=[tool_chunk(response_calls[index], '', '', delta['partial_json'])])
                elif kind == 'content_block_stop':
                    index = event['index']
                    if index in closed_blocks:
                        raise ValueError('Messages content block stopped twice.')
                    closed_blocks.add(index)
                    block = blocks[index]
                    if block['type'] == 'tool_use':
                        arguments = partial_json.get(index, json.dumps(block.get('input', {})))
                        try:
                            block['input'] = json.loads(arguments)
                        except ValueError:
                            # Preserve the raw call for whole-batch validation and
                            # bounded protocol repair; never replay malformed input.
                            replay_valid = False
                        if index not in partial_json:
                            yield chunk(calls=[tool_chunk(response_calls[index], '', '', arguments)])
                elif kind == 'message_delta':
                    usage.update(event.get('usage') or {})
                    reason = event.get('delta', {}).get('stop_reason')
                    if reason is not None:
                        finish = {'end_turn': 'stop', 'stop_sequence': 'stop', 'tool_use': 'tool_calls',
                                  'max_tokens': 'length', 'refusal': 'content_filter',
                                  'model_context_window_exceeded': 'incomplete'}.get(reason, 'incomplete')
                elif kind == 'message_stop':
                    if set(blocks) != closed_blocks:
                        raise ValueError('Messages response contains unfinished content blocks.')
                    if finish:
                        yield chunk(finish=finish, usage=usage_value(usage, self.protocol),
                                    history={'protocol': self.protocol, 'model': self.model,
                                             'items': [blocks[i] for i in sorted(blocks)]} if replay_valid else None)
                    return


def complete_response(result, protocol):
    text, calls = [], []
    items = result.get('output', []) if protocol == 'responses' else result.get('content', [])
    for item in items:
        kind = item.get('type')
        if kind == 'message':
            text.extend(p.get('text', '') for p in item.get('content', []) if p.get('type') == 'output_text')
        elif kind == 'text':
            text.append(item.get('text', ''))
        elif kind in {'function_call', 'tool_use'}:
            calls.append(item)
    if protocol == 'responses':
        finish = 'stop' if result.get('status') == 'completed' else 'length'
    else:
        finish = 'stop' if result.get('stop_reason') in {'end_turn', 'stop_sequence'} else 'length'
    return NS(choices=[NS(finish_reason=finish, message=NS(content=''.join(text), tool_calls=calls))])


class ModelClient:
    """A per-task snapshot; native replay data never crosses protocols/models."""
    def __init__(self, config, key, *, chat_factory, chat_options, timeout=None):
        timeout = timeout if timeout is not None else getattr(config, 'model_idle_timeout_seconds', 120)
        self.config = config.model_copy(deep=True)
        self.key, self.timeout = key, timeout
        self.protocol = getattr(config, 'protocol', 'chat')
        self.sdk = chat_factory(api_key=key, **chat_options(config, timeout=timeout)) if self.protocol == 'chat' else None
        self.http = None
        self.chat = NS(completions=self)

    async def __aenter__(self):
        if self.sdk:
            await self.sdk.__aenter__()
        else:
            self.http = httpx.AsyncClient(timeout=self.timeout, follow_redirects=False)
        return self

    async def __aexit__(self, *args):
        if self.sdk:
            return await self.sdk.__aexit__(*args)
        await self.http.aclose()

    async def create(self, **options):
        if self.sdk:
            validate_history(options['messages'], 'chat', options['model'])
            options['messages'] = [{k: v for k, v in message.items() if k not in {HISTORY_FIELD, 'is_error'}}
                                   for message in options['messages']]
            return await self.sdk.chat.completions.create(**options)
        payload = responses_request(options) if self.protocol == 'responses' else claude_request(options)
        if self.protocol == 'claude' and options.get('reasoning_effort') and httpx.URL(self.config.base_url).host == 'api.deepseek.com':
            if options['reasoning_effort'] == 'medium':
                raise ValueError('DeepSeek Messages 思考强度请选择 low 或 high，或使用默认值。')
            payload['thinking'] = {'type': 'enabled'}
        request = self.http.build_request('POST', chat_endpoint(self.config), json=payload,
                                         headers=auth_headers(self.config, self.key))
        response = await self.http.send(request, stream=True)
        try:
            if not response.is_success:
                # Bound error bodies as well as successful output.
                body = await bounded_body(response)
                raise APIStatusError(body.decode('utf-8', errors='replace')[:4096], response=response, body=None)
            if payload['stream']:
                if 'text/event-stream' not in response.headers.get('content-type', ''):
                    raise ValueError('Model service did not return an SSE stream.')
                return NativeStream(response, self.protocol, options['model'])
            result = json.loads(await bounded_body(response))
            return complete_response(result, self.protocol)
        finally:
            if not response.is_success or not payload['stream']:
                await response.aclose()
            elif 'text/event-stream' not in response.headers.get('content-type', ''):
                await response.aclose()


async def bounded_body(response):
    body = bytearray()
    async for part in response.aiter_bytes():
        body.extend(part)
        if len(body) > MAX_RESPONSE_BYTES:
            raise ValueError('Model response exceeded the size limit.')
    return bytes(body)
