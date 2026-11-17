"""Detect and recover provider tool markup that arrived in answer text.

Providers occasionally serialize a native tool call into the text channel (DeepSeek
DSML is the common case).  The harness keeps the operation atomic: a complete,
schema-valid envelope is converted into a native call only when every call in it
can be recovered; partial or ambiguous text is never guessed at.
"""
from __future__ import annotations

import ast
import json
import re
import uuid
from dataclasses import dataclass
from html import unescape
from typing import Any

PIPES = r'|\uff5c\u2223\u2758'
_PIPE = r'[' + PIPES + r']'
_PIPE_SPACE = r'[\s' + PIPES + r']*'
# Wait for a delimiter after DSML so names such as DSMLParser remain ordinary prose.
DSML_MARKER = re.compile(r'<\s*/?\s*' + _PIPE + r'+' + _PIPE_SPACE + r'DSML(?=[\s' + PIPES + r'>]|$)', re.I)
MARKER_PREFIX = re.compile(r'<\s*/?\s*(?:' + _PIPE + r'+' + _PIPE_SPACE + r'(?:D(?:S(?:M(?:L)?)?)?)?)?$', re.I)
MAX_PENDING = 256
PROTOCOL_NOTICE = '[模型返回了工具协议文本；该文本未执行。]'
HISTORY_NOTICE = '[Historical assistant tool markup omitted. It was text, not evidence of execution; recheck live state.]'

# A DSML tag looks like <|DSML|invoke ...> but tolerant of spaces and full-width bars.
_DSML_TAG = r'<\s*' + _PIPE + r'+\s*DSML\s*' + _PIPE + r'+\s*'
# DeepSeek and some gateways emit both <|DSML|tool_calls> and the shorter
# <|DSML| calls> spelling, often with full-width bars and spaces.
_DSML_CALLS_NAME = r'(?:tool[_\s]*calls?|function[_\s]*calls?|calls?)'
_DSML_TOOL_CALLS = re.compile(
    _DSML_TAG + _DSML_CALLS_NAME + r'\s*>', re.I)
_DSML_TOOL_CALLS_END = re.compile(
    r'<\s*/\s*' + _PIPE + r'+\s*DSML\s*' + _PIPE + r'+\s*' + _DSML_CALLS_NAME + r'\s*>', re.I)
_DSML_INVOKE = re.compile(
    _DSML_TAG + r'invoke\s+name\s*=\s*(["\'])(.*?)\1[^>]*>', re.I | re.S)
_DSML_INVOKE_END = re.compile(
    r'<\s*/\s*' + _PIPE + r'+\s*DSML\s*' + _PIPE + r'+\s*invoke\s*>', re.I)
_DSML_PARAM = re.compile(
    _DSML_TAG + r'parameter\s+name\s*=\s*(["\'])(.*?)\1[^>]*>(.*?)'
    r'<\s*/\s*' + _PIPE + r'+\s*DSML\s*' + _PIPE + r'+\s*parameter\s*>', re.I | re.S)

# The XML-looking fallback used by several OpenAI-compatible gateways.
_XML_TOOL_CALLS = re.compile(
    r'<\s*(?:tool_calls|function_calls)\b[^>]*>(.*?)<\s*/\s*(?:tool_calls|function_calls)\s*>', re.I | re.S)
_XML_INVOKE = re.compile(
    r'<\s*(?:invoke|tool_use)\s+name\s*=\s*(["\'])(.*?)\1[^>]*>(.*?)<\s*/\s*(?:invoke|tool_use)\s*>',
    re.I | re.S)
_XML_PARAM = re.compile(
    r'<\s*(?:parameter|param|argument)\s+name\s*=\s*(["\'])(.*?)\1[^>]*>(.*?)'
    r'<\s*/\s*(?:parameter|param|argument)\s*>', re.I | re.S)
_JSON_FENCE = re.compile(r'```(?:json)?\s*(.*?)```', re.I | re.S)
# Some gateways use a singular block around a JSON call.
_XML_SINGLE_CALL = re.compile(
    r'<\s*(tool_call|function_call|tool_use)\b[^>]*>(.*?)<\s*/\s*\1\s*>', re.I | re.S)
# Conservative Python-like invocation: only a whole answer or one fenced block.
_FUNCTION_CALL = re.compile(r'^\s*([A-Za-z_][A-Za-z0-9_]*)\s*\((.*)\)\s*$', re.S)
_JSON_CALL_MARKERS = ('"tool_calls"', '"function_calls"', '"function_call"', '"tool_use"')


def _json_may_be_call(text: str) -> bool:
    if any(marker in text for marker in _JSON_CALL_MARKERS):
        return True
    return bool(re.search(r'"(?:name|tool|tool_name)"\s*:', text)
                and re.search(r'"(?:arguments|parameters|input|args|kwargs)"\s*:', text))


@dataclass(frozen=True)
class RecoveredCall:
    name: str
    arguments: dict


def has_tool_markup(text: str) -> bool:
    return DSML_MARKER.search(text) is not None


def _coerce(value: Any, schema: dict) -> Any:
    """Convert one textual parameter to the type advertised by its JSON schema."""
    kind = schema.get('type')
    if isinstance(kind, list):
        kind = next((item for item in kind if item != 'null'), kind[0] if kind else None)
    if isinstance(value, str):
        stripped = value.strip()
        if kind in {'object', 'array'} or stripped[:1] in {'{', '['}:
            try:
                return json.loads(stripped)
            except (ValueError, TypeError):
                return value
        if kind == 'integer':
            try:
                return int(stripped, 10)
            except ValueError:
                return value
        if kind == 'number':
            try:
                return float(stripped)
            except ValueError:
                return value
        if kind == 'boolean':
            if stripped.lower() in {'true', '1', 'yes', 'on'}:
                return True
            if stripped.lower() in {'false', '0', 'no', 'off'}:
                return False
            return value
        if kind == 'null' and stripped.lower() in {'null', 'none', ''}:
            return None
        return value
    return value


def _parameter_values(body: str, pattern: re.Pattern, schema: dict) -> dict:
    values = {}
    for match in pattern.finditer(body):
        name = unescape(match.group(2)).strip()
        if not name:
            continue
        values[name] = _coerce(unescape(match.group(3)), schema.get('properties', {}).get(name, {}))
    return values


def _strip_ranges(text: str, ranges: list[tuple[int, int]]) -> str:
    merged: list[tuple[int, int]] = []
    for start, end in sorted(ranges):
        if merged and start <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(merged[-1][1], end))
        else:
            merged.append((start, end))
    cleaned = text
    for start, end in reversed(merged):
        cleaned = cleaned[:start] + cleaned[end:]
    return cleaned.strip()


def _dsml_calls(text: str, advertised: dict[str, dict]) -> tuple[str, list[RecoveredCall]]:
    ranges: list[tuple[int, int]] = []
    calls: list[RecoveredCall] = []
    opens = list(_DSML_TOOL_CALLS.finditer(text))
    if opens:
        for open_match in opens:
            close = _DSML_TOOL_CALLS_END.search(text, open_match.end())
            if not close:
                # Tolerate a missing outer close when every invoke inside it is
                # complete; the recovered calls are still validated atomically.
                recovered_any = False
                for invoke in _DSML_INVOKE.finditer(text, open_match.end()):
                    end = _DSML_INVOKE_END.search(text, invoke.end())
                    if not end:
                        raise ValueError('incomplete_textual_tool_call')
                    name = invoke.group(2).strip()
                    params = _parameter_values(text[invoke.end():end.start()], _DSML_PARAM,
                                               advertised.get(name, {}))
                    calls.append(RecoveredCall(name, params))
                    ranges.append((open_match.start(), end.end()))
                    recovered_any = True
                if not recovered_any:
                    raise ValueError('incomplete_textual_tool_call')
                continue
            body = text[open_match.end():close.start()]
            for invoke in _DSML_INVOKE.finditer(body):
                end = _DSML_INVOKE_END.search(body, invoke.end())
                if not end:
                    raise ValueError('incomplete_textual_tool_call')
                name = invoke.group(2).strip()
                params = _parameter_values(body[invoke.end():end.start()], _DSML_PARAM,
                                           advertised.get(name, {}))
                calls.append(RecoveredCall(name, params))
            ranges.append((open_match.start(), close.end()))
    else:
        # Some gateways omit the outer tool_calls element but keep complete invoke tags.
        for invoke in _DSML_INVOKE.finditer(text):
            end = _DSML_INVOKE_END.search(text, invoke.end())
            if not end:
                raise ValueError('incomplete_textual_tool_call')
            name = invoke.group(2).strip()
            params = _parameter_values(text[invoke.end():end.start()], _DSML_PARAM,
                                       advertised.get(name, {}))
            calls.append(RecoveredCall(name, params))
            ranges.append((invoke.start(), end.end()))
    if not calls:
        raise ValueError('textual_tool_call_without_invoke')
    return _strip_ranges(text, ranges), calls


def _call_name(value: dict) -> str | None:
    for key in ('name', 'tool', 'tool_name', 'toolName'):
        name = value.get(key)
        if isinstance(name, str) and name.strip():
            return name.strip()
    function = value.get('function')
    if isinstance(function, str) and function.strip():
        return function.strip()
    if isinstance(function, dict):
        name = function.get('name')
        if isinstance(name, str) and name.strip():
            return name.strip()
    return None


def _call_arguments(value: dict) -> Any:
    function = value.get('function')
    if isinstance(function, dict) and any(key in function for key in ('arguments', 'parameters', 'input', 'args')):
        value = function
    for key in ('arguments', 'parameters', 'input', 'args', 'kwargs'):
        if key in value:
            return value[key]
    return {}


def _calls_from_value(value: Any, advertised: dict[str, dict]) -> list[RecoveredCall]:
    """Normalize common textual JSON shapes into recovered calls."""
    if isinstance(value, list):
        calls = []
        for item in value:
            calls.extend(_calls_from_value(item, advertised))
        return calls
    if not isinstance(value, dict):
        raise ValueError('invalid_textual_call')
    for key in ('tool_calls', 'function_calls', 'tool_use', 'calls'):
        if key in value:
            return _calls_from_value(value[key], advertised)
    if 'function_call' in value:
        return _calls_from_value(value['function_call'], advertised)
    name = _call_name(value)
    if name is None:
        raise ValueError('invalid_textual_call')
    if name not in advertised:
        # Keep the name so the normal validator can return an actionable error.
        pass
    arguments = _call_arguments(value)
    if isinstance(arguments, str):
        try:
            arguments = json.loads(arguments)
        except (ValueError, TypeError):
            raise ValueError('invalid_textual_arguments')
    if arguments is None:
        arguments = {}
    if not isinstance(arguments, dict):
        raise ValueError('invalid_textual_arguments')
    return [RecoveredCall(name, arguments)]


def _xml_calls(text: str, advertised: dict[str, dict]) -> tuple[str, list[RecoveredCall]]:
    ranges: list[tuple[int, int]] = []
    calls: list[RecoveredCall] = []
    for block in _XML_TOOL_CALLS.finditer(text):
        for invoke in _XML_INVOKE.finditer(block.group(1)):
            name = invoke.group(2).strip()
            body = invoke.group(3)
            params = _parameter_values(body, _XML_PARAM, advertised.get(name, {}))
            # Bare <invoke name="x">{"json": "args"}</invoke> is also accepted.
            if not params and body.strip():
                try:
                    value = json.loads(body.strip())
                    if isinstance(value, dict):
                        params = value
                except (ValueError, TypeError):
                    pass
            calls.append(RecoveredCall(name, params))
        ranges.append((block.start(), block.end()))
    # Singular <tool_call> / <function_call> / <tool_use> blocks are common in
    # gateways that cannot use the native channel. Their body may be JSON or
    # the same invoke/parameter markup as the plural envelope.
    for block in _XML_SINGLE_CALL.finditer(text):
        if any(start <= block.start() < end for start, end in ranges):
            continue
        body = block.group(2).strip()
        try:
            value = json.loads(body)
        except (ValueError, TypeError):
            value = None
        if value is not None:
            calls.extend(_calls_from_value(value, advertised))
        else:
            released = False
            for invoke in _XML_INVOKE.finditer(body):
                name = invoke.group(2).strip()
                params = _parameter_values(invoke.group(3), _XML_PARAM, advertised.get(name, {}))
                calls.append(RecoveredCall(name, params))
                released = True
            if not released:
                raise ValueError('invalid_textual_call')
        ranges.append((block.start(), block.end()))
    if not calls:
        # A gateway may emit only complete invoke tags without the outer element.
        for invoke in _XML_INVOKE.finditer(text):
            name = invoke.group(2).strip()
            body = invoke.group(3)
            params = _parameter_values(body, _XML_PARAM, advertised.get(name, {}))
            if not params and body.strip():
                try:
                    value = json.loads(body.strip())
                    if isinstance(value, dict):
                        params = value
                except (ValueError, TypeError):
                    pass
            calls.append(RecoveredCall(name, params))
            ranges.append((invoke.start(), invoke.end()))
    if not calls:
        raise ValueError('textual_tool_call_without_invoke')
    return _strip_ranges(text, ranges), calls


def _json_candidates(text: str):
    stripped = text.strip()
    if stripped:
        yield stripped
    for fenced in _JSON_FENCE.finditer(text):
        body = fenced.group(1).strip()
        if body:
            yield body
    # A pure call envelope may sit inside prose. Extract balanced JSON spans.
    decoder = json.JSONDecoder()
    offset = 0
    seen = set()
    while offset < len(text):
        match = re.search(r'[\[{]', text[offset:])
        if not match:
            break
        start = offset + match.start()
        try:
            _, length = decoder.raw_decode(text[start:])
        except (ValueError, TypeError):
            offset = start + 1
            continue
        candidate = text[start:start + length].strip()
        if candidate and candidate not in seen:
            seen.add(candidate)
            yield candidate
        offset = start + length


def _json_calls(text: str, advertised: dict[str, dict]) -> tuple[str, list[RecoveredCall]]:
    last_error: ValueError | None = None
    for candidate in _json_candidates(text):
        try:
            value = json.loads(candidate)
        except (ValueError, TypeError):
            last_error = ValueError('textual_tool_call_without_json')
            continue
        try:
            calls = _calls_from_value(value, advertised)
        except ValueError as exc:
            last_error = exc
            continue
        if not calls:
            continue
        if candidate == text.strip():
            return '', calls
        # Remove the fenced block (or the found JSON span) from visible prose.
        index = text.find(candidate)
        if index >= 0:
            end = index + len(candidate)
            fence = _JSON_FENCE.search(text[max(0, index - 3):end + 3])
            if fence:
                base = max(0, index - 3)
                return (text[:base + fence.start()] + text[base + fence.end():]).strip(), calls
            return (text[:index] + text[end:]).strip(), calls
        return text, calls
    raise last_error or ValueError('textual_tool_call_without_json')


def _function_calls(text: str, advertised: dict[str, dict]) -> tuple[str, list[RecoveredCall]]:
    """Recover a single conservative ``tool(kw=value, ...)`` invocation."""
    def parse(candidate: str):
        match = _FUNCTION_CALL.fullmatch(candidate)
        if not match:
            return None
        name, arguments = match.group(1), match.group(2).strip()
        if name not in advertised:
            return None
        try:
            expression = ast.parse(f"f({arguments})", mode='eval').body
        except SyntaxError:
            return None
        if not isinstance(expression, ast.Call) or not isinstance(expression.func, ast.Name):
            return None
        if expression.keywords and expression.args:
            return None
        if any(keyword.arg is None for keyword in expression.keywords):
            return None
        values = {}
        if expression.args:
            if len(expression.args) != 1 or expression.keywords:
                return None
            try:
                positional = ast.literal_eval(expression.args[0])
            except (ValueError, TypeError, SyntaxError):
                return None
            if not isinstance(positional, dict):
                return None
            values = positional
        for keyword in expression.keywords:
            try:
                values[keyword.arg] = ast.literal_eval(keyword.value)
            except (ValueError, TypeError, SyntaxError):
                return None
        return RecoveredCall(name, values)

    candidate = text.strip()
    call = parse(candidate)
    if call is not None:
        return '', [call]
    for fenced in _JSON_FENCE.finditer(text):
        body = fenced.group(1).strip()
        call = parse(body)
        if call is not None:
            return (text[:fenced.start()] + text[fenced.end():]).strip(), [call]
    raise ValueError('textual_tool_call_without_invoke')


def recover_textual_calls(content: str, schemas: list[dict]) -> tuple[str, list[dict]]:
    """Return answer text with complete textual calls removed and native-shaped calls.

    ``schemas`` uses the API tool declaration shape.  The caller still runs the
    normal validation and live-registry checks, so recovery never grants access
    to a tool that was not advertised for this round.
    """
    advertised = {tool['function']['name']: tool['function']['parameters'] for tool in schemas}
    stripped = content.strip()
    has_dsml = has_tool_markup(content)
    has_xml = bool(_XML_TOOL_CALLS.search(content) or _XML_SINGLE_CALL.search(content)
                   or _XML_INVOKE.search(content))
    has_json = _json_may_be_call(content) and (
        stripped.startswith('{') or stripped.startswith('[')
        or bool(_JSON_FENCE.search(content)) or bool(re.search(r'[\[{]', content)))
    has_function = bool(_FUNCTION_CALL.fullmatch(stripped)) or any(
        _FUNCTION_CALL.fullmatch(fenced.group(1).strip()) for fenced in _JSON_FENCE.finditer(content))
    if not (has_dsml or has_xml or has_json or has_function):
        return content, []
    try:
        if has_dsml:
            cleaned, recovered = _dsml_calls(content, advertised)
        elif has_xml:
            cleaned, recovered = _xml_calls(content, advertised)
        elif has_json:
            try:
                cleaned, recovered = _json_calls(content, advertised)
            except ValueError:
                if not has_function:
                    raise
                cleaned, recovered = _function_calls(content, advertised)
        else:
            cleaned, recovered = _function_calls(content, advertised)
    except ValueError as exc:
        raise ValueError(f'textual_tool_protocol: {exc}. Provide one complete call envelope with advertised names and JSON arguments.') from exc
    calls = []
    for index, call in enumerate(recovered, 1):
        calls.append({
            'id': f'textual_{uuid.uuid4().hex[:12]}',
            'type': 'function',
            'function': {'name': call.name, 'arguments': json.dumps(call.arguments, ensure_ascii=False)},
        })
    return cleaned, calls



def assistant_history_text(text: str) -> str:
    """Sanitize model input only; leave persisted history and user attachments untouched."""
    starts = [match.start() for match in (
        DSML_MARKER.search(text), _XML_TOOL_CALLS.search(text),
        _XML_SINGLE_CALL.search(text), _XML_INVOKE.search(text)) if match]
    if starts:
        return HISTORY_NOTICE + '\n' + text[:min(starts)]
    return text


class AnswerFilter:
    """Only buffer a possible marker prefix, not all text following a less-than sign."""

    def __init__(self):
        self.pending = ''
        self.blocked = False

    def feed(self, text: str) -> str:
        if self.blocked:
            return ''
        self.pending += text
        parts = []
        while self.pending:
            index = self.pending.find('<')
            if index < 0:
                parts.append(self.pending)
                self.pending = ''
                break
            parts.append(self.pending[:index])
            self.pending = self.pending[index:]
            match = DSML_MARKER.match(self.pending)
            if match and match.end() < len(self.pending):
                self.pending = ''
                self.blocked = True
                parts.append(PROTOCOL_NOTICE)
                break
            if MARKER_PREFIX.fullmatch(self.pending):
                if len(self.pending) > MAX_PENDING:
                    raise ValueError('模型工具协议前缀异常过长，本轮未执行。')
                break
            # This angle bracket is ordinary prose/HTML. Continue scanning later ones.
            parts.append('<')
            self.pending = self.pending[1:]
        return ''.join(parts)

    def finish(self) -> str:
        visible, self.pending = self.pending, ''
        if has_tool_markup(visible):
            self.blocked = True
            return PROTOCOL_NOTICE
        return visible
