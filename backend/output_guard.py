"""Atomic validation of model output against the current API tool advertisement."""
from __future__ import annotations

import json
import math
import re

from jsonschema import Draft202012Validator, ValidationError

from .tool_protocol import has_tool_markup

MAX_PROTOCOL_RETRIES = 2
TOOL_TAG = re.compile(
    r'<\s*/?\s*(?:tool_calls?|function_calls?|invoke|tool_use|function)\b[^>]*[>\n]', re.I)


class ProtocolViolation(ValueError):
    """A model response must be regenerated before any part can be executed."""


def strict_arguments(raw: str) -> dict:
    def unique_object(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError('Duplicate JSON key')
            result[key] = value
        return result

    def invalid_constant(value):
        raise ValueError('Non-finite JSON number')

    def finite(value):
        if isinstance(value, float) and not math.isfinite(value):
            raise ValueError('Non-finite JSON number')
        if isinstance(value, dict):
            for child in value.values():
                finite(child)
        elif isinstance(value, list):
            for child in value:
                finite(child)

    try:
        if not isinstance(raw, str):
            raise ValueError('Arguments must be JSON text on the API wire')
        arguments = json.loads(raw, object_pairs_hook=unique_object, parse_constant=invalid_constant)
        if not isinstance(arguments, dict):
            raise ValueError('Arguments must decode to an object')
        finite(arguments)
        return arguments
    except (ValueError, TypeError, RecursionError) as exc:
        raise ProtocolViolation('invalid_arguments_json: use one strict JSON object, without duplicate keys or non-finite numbers.') from exc


def contains_textual_call(text: str, names=()) -> bool:
    if has_tool_markup(text) or TOOL_TAG.search(text):
        return True
    # Match executable-looking envelopes, not ordinary prose mentioning tool_calls.
    decoder = json.JSONDecoder()

    def envelope(value):
        if isinstance(value, list):
            return any(envelope(item) for item in value)
        if not isinstance(value, dict):
            return False
        if any(key in value for key in ('tool_calls', 'function_call', 'tool_use')):
            return True
        if isinstance(value.get('name'), str) and any(key in value for key in ('arguments', 'parameters', 'input')):
            return True
        return any(envelope(child) for child in value.values())

    offset = 0
    while offset < len(text):
        match = re.search(r'[\[{]', text[offset:])
        if not match:
            break
        start = offset + match.start()
        try:
            value, length = decoder.raw_decode(text[start:])
        except (ValueError, RecursionError):
            offset = start + 1
            continue
        if envelope(value):
            return True
        offset = start + length
    # Also catch truncated/fenced API envelopes and literal calls to advertised tools.
    if re.search(r'["\'](?:tool_calls|function_call)["\']\s*:\s*[\[{]', text):
        return True
    # A quoted inline example inside prose is documentation, not a dispatch.
    # Keep standalone/fenced calls rejected; markup/envelopes above are always checked.
    call_text = []
    in_fence = False
    for line in text.splitlines():
        if re.match(r'^\s*(?:`{3,}|~{3,})', line):
            in_fence = not in_fence
        if not in_fence:
            prose = re.sub(r'(?<!`)`[^`\n]+`(?!`)', '', line)
            if re.search(r'[^\W\d_]', prose, re.UNICODE):
                line = prose
        call_text.append(line)
    return any(re.search(r'(?<![\w])' + re.escape(name) + r'\s*\(', '\n'.join(call_text)) for name in names)


def validate_response(content: str, calls: list[dict], schemas: list[dict], *,
                      require_call: bool = False, used_ids=(), call_budget: int | None = None,
                      defer_unregistered: bool = False) -> None:
    advertised = {tool['function']['name']: tool['function']['parameters'] for tool in schemas}
    if contains_textual_call(content, advertised):
        raise ProtocolViolation('textual_tool_protocol: keep DSML/XML/JSON calls and executable tool syntax out of content; use native structured tool operations only.')
    if require_call and not calls:
        raise ProtocolViolation('missing_structured_call: a prose explanation is not a repaired API tool call.')
    if call_budget is not None and len(calls) > call_budget:
        raise ProtocolViolation('tool_budget: reduce the batch to the remaining call allowance.')
    seen = set(used_ids)
    for call in calls:
        if not isinstance(call, dict) or call.get('type') != 'function':
            raise ProtocolViolation('invalid_call_envelope: use the selected API native tool operation with a valid identity, name and arguments.')
        call_id = call.get('id')
        if not isinstance(call_id, str) or not call_id.strip() or call_id in seen:
            raise ProtocolViolation('invalid_call_id: tool call IDs must be non-empty and unique.')
        seen.add(call_id)
        function = call.get('function')
        if (not isinstance(function, dict) or not isinstance(function.get('name'), str)
                or function['name'] not in advertised):
            name = function.get('name') if isinstance(function, dict) else None
            # dsh-style per-call dispatch: an unknown name becomes an UNKNOWN_TOOL
            # result for that call, so valid siblings and the turn continue.
            if defer_unregistered and isinstance(name, str) and name.strip():
                continue
            # Echo only identifier-shaped names, never arbitrary rejected instructions.
            label = name if isinstance(name, str) and re.fullmatch(r'[A-Za-z0-9_-]{1,128}', name) else '(invalid name)'
            allowed = ', '.join(advertised)[:800]
            discovery = (f'Call discover_tools with query={label} separately, then wait for the next request. '
                         if 'discover_tools' in advertised else
                         'Discovery is unavailable in this request; use an advertised tool or explain the missing capability. ')
            raise ProtocolViolation(
                f'unregistered_tool: {label} is absent from the current tools field. '
                + discovery + f'Currently advertised names: {allowed}.')
        arguments = strict_arguments(function.get('arguments'))
        try:
            Draft202012Validator(advertised[function['name']]).validate(arguments)
        except ValidationError as exc:
            # Return bounded schema guidance, never the rejected payload or its instructions.
            path = '/'.join(str(part) for part in exc.absolute_path)[:100] or '(root)'
            expected = json.dumps(exc.validator_value, ensure_ascii=True)[:500]
            detail = ''
            if exc.validator == 'additionalProperties' and exc.validator_value is False and isinstance(exc.instance, dict):
                properties = exc.schema.get('properties', {})
                patterns = exc.schema.get('patternProperties', {})
                extras = [key for key in exc.instance if key not in properties
                          and not any(re.search(pattern, key) for pattern in patterns)]
                # Echo bounded identifier-shaped field names only, never values or arbitrary instructions.
                def labels(keys):
                    return json.dumps([key if re.fullmatch(r'[A-Za-z_][A-Za-z0-9_-]{0,63}', key)
                                       else '(non-identifier field)' for key in sorted(keys)[:16]])[:800]
                detail = f'Remove unexpected fields: {labels(extras)}; allowed named fields: {labels(properties)}; '
                if patterns:
                    detail += 'patternProperties fields are also allowed by the schema; '
            elif exc.validator == 'required' and isinstance(exc.instance, dict):
                missing = [key for key in exc.validator_value if key not in exc.instance]
                detail = 'Missing required fields: ' + json.dumps(missing, ensure_ascii=True)[:500] + '; '
            raise ProtocolViolation(
                f'argument_schema: {function["name"]} at {path} violates {exc.validator}; '
                f'{detail}expected {expected}; follow its current parameters schema.') from exc


def repair_guidance(error: ValueError, schemas: list[dict]) -> str:
    """Bounded, application-owned feedback, never an invented tool result."""
    code = str(error).split(':', 1)[0]
    instructions = {
        'invalid_call_envelope': 'Use the native tool operation structure for the selected protocol.',
        'textual_tool_protocol': 'Send operations through the native tool channel; keep answer text as prose.',
        'missing_structured_call': 'The repair requires an actual structured operation, not a promise.',
        'invalid_arguments_json': 'Regenerate arguments as one JSON object with unique keys and finite values.',
        'argument_schema': 'Correct the named argument path using the advertised schema; do not invent missing task facts.',
        'unavailable_tool': 'The live registry changed. Use the refreshed tool list; old declarations grant no access.',
        'invalid_call_id': 'Generate fresh non-empty call identifiers; do not reuse completed calls.',
        'missing_purpose': 'Explain the target and intended change in _forma_purpose; normal approval is still required.',
        'clarification_batch': 'Submit the clarification alone without any CAD operations.',
        'tool_budget': 'Reduce the batch to the remaining allowance.',
    }
    guidance = instructions.get(code, 'Regenerate only the rejected response against this request declarations.')
    names = {tool['function']['name'] for tool in schemas}
    if code == 'unregistered_tool':
        guidance = ('Discover the missing name in a separate batch, then wait for the next request.'
                    if 'discover_tools' in names else
                    'No discovery tool is available; select a declared capability, or clarify the limitation.')
    return ('Validation outcome: not_executed. Failure category: ' +
            (code if code in instructions or code == 'unregistered_tool' else 'invalid_response') + '. ' + guidance)
