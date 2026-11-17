"""Single source for advertised arguments, display metadata and executor input."""
from __future__ import annotations

import copy
from dataclasses import dataclass

from jsonschema import Draft202012Validator

from .tool_purpose import PURPOSE_FIELD, PURPOSE_SCHEMA, explained_tool, split_purpose

DISCOVER_BASE = {'type': 'function', 'function': {
    'name': 'discover_tools',
    'description': 'Find available SolidWorks or external MCP tools by exact names, service name or task description. Help text is not registration. Returns schemas and enables matches on the NEXT request; call discovery separately before using a new tool.',
    'parameters': {'type': 'object', 'properties': {'query': {'type': 'string'}},
                   'required': ['query'], 'additionalProperties': False}}}


@dataclass(frozen=True)
class ToolContract:
    declaration: dict
    input_schema: dict

    @property
    def name(self):
        return self.declaration['function']['name']

    @property
    def schema(self):
        return copy.deepcopy(self.declaration)

    def prepare(self, arguments):
        # Validate metadata before stripping it; execution never sees display-only fields.
        Draft202012Validator(self.declaration['function']['parameters']).validate(arguments)
        args, purpose = split_purpose(arguments)
        Draft202012Validator(self.input_schema).validate(args)
        return args, purpose


def local_contract(spec: dict) -> ToolContract:
    declaration = copy.deepcopy(spec)
    schema = declaration['function']['parameters']
    original = copy.deepcopy(schema)
    if PURPOSE_FIELD in schema.get('properties', {}):
        raise ValueError('Local tool collides with reserved display metadata')
    schema.setdefault('properties', {})[PURPOSE_FIELD] = copy.deepcopy(PURPOSE_SCHEMA)
    return ToolContract(declaration, original)


def cad_contract(tool: dict) -> ToolContract:
    decorated = explained_tool(tool)
    return ToolContract({'type': 'function', 'function': {
        'name': tool['name'], 'description': tool.get('description', '')[:5000],
        'parameters': decorated['inputSchema']}}, copy.deepcopy(tool.get('inputSchema', {})))


DISCOVER = local_contract(DISCOVER_BASE).schema
