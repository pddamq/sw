"""Model-only operation explanations; never forward these fields to the CAD executor."""
from __future__ import annotations

import copy

from .operation_guard import ALWAYS_CONFIRM

PURPOSE_FIELD = '_forma_purpose'
PURPOSE_SCHEMA = {
    'type': 'string', 'minLength': 1, 'maxLength': 600, 'pattern': r'\S',
    'description': "Explain in the user's language what this specific call will do, to which target, "
                   'and why it is needed for the user request. Mention expected changes and file effects. '
                   'Use plain language, not just the tool name. Describe intended effects, not completed results. '
                   'Required when user confirmation is needed. This explanation is displayed on the approval card.',
}


def explained_tool(tool: dict) -> dict:
    result = copy.deepcopy(tool)
    schema = result.setdefault('inputSchema', {'type': 'object', 'properties': {}})
    properties = schema.setdefault('properties', {})
    if PURPOSE_FIELD in properties:
        raise ValueError('Executor schema conflicts with reserved operation explanation field')
    properties[PURPOSE_FIELD] = copy.deepcopy(PURPOSE_SCHEMA)
    if tool['name'] in ALWAYS_CONFIRM:
        schema.setdefault('required', []).append(PURPOSE_FIELD)
    return result


def split_purpose(arguments: dict) -> tuple[dict, str | None]:
    arguments = copy.deepcopy(arguments)
    purpose = arguments.pop(PURPOSE_FIELD, None)
    if purpose is not None:
        if not isinstance(purpose, str) or not purpose.strip() or len(purpose) > 600:
            raise ValueError('Operation purpose must be a non-empty explanation of at most 600 characters.')
        purpose = purpose.strip()
    return arguments, purpose
