"""Contract projection is identical across native transports and execution."""
import copy
import pytest
from jsonschema import ValidationError

from backend.tool_contracts import cad_contract, local_contract, DISCOVER_BASE
from backend.knowledge_tools import TOOLS
from backend.user_questions import CLARIFY
from backend.model_transport import responses_request, claude_request
from backend.tool_snapshot import ToolSnapshot

SPECS = [DISCOVER_BASE, CLARIFY, *TOOLS.values()]
ARGS = [{'query': 'status'}, {'question': 'Choose', 'reason': 'Missing'}, {},
        {'query': 'steel'}, {'document_id': 'a' * 32}]


@pytest.mark.parametrize('spec,args', list(zip(SPECS, ARGS)))
def test_local_metadata_declaration_validation_and_execution_share_contract(spec, args):
    original = copy.deepcopy(spec)
    contract = local_contract(spec)
    snapshot = ToolSnapshot([contract.schema], contracts=[contract])
    payload = {**args, '_forma_purpose': 'Explain the request'}
    assert snapshot.prepare(contract.name, payload) == (args, 'Explain the request')
    assert spec == original and '_forma_purpose' in payload
    options = {'model': 'fixture', 'messages': [{'role': 'user', 'content': 'test'}],
               'max_tokens': 100, 'tools': snapshot.schemas}
    expected = contract.schema['function']['parameters']
    assert responses_request(options)['tools'][0]['parameters'] == expected
    assert claude_request(options)['tools'][0]['input_schema'] == expected
    for value in [None, '', ' ', 1, 'x' * 601]:
        with pytest.raises(ValidationError):
            snapshot.prepare(contract.name, {**args, '_forma_purpose': value})
    with pytest.raises(ValidationError):
        snapshot.prepare(contract.name, {**args, 'unexpected': True})


def test_cad_nested_constraints_and_snapshot_detachment():
    tool = {'name': 'fixture', 'inputSchema': {'type': 'object', 'properties': {
        'values': {'type': 'array', 'items': {'type': 'integer', 'minimum': 1}}},
        'required': ['values'], 'additionalProperties': False}}
    contract = cad_contract(tool)
    snapshot = ToolSnapshot([contract.schema], [tool], [contract])
    contract.declaration['function']['parameters']['properties'].clear()
    tool['inputSchema']['properties'].clear()
    assert snapshot.prepare('fixture', {'values': [1], '_forma_purpose': 'inspect'}) == ({'values': [1]}, 'inspect')
    with pytest.raises(ValidationError):
        snapshot.prepare('fixture', {'values': [0]})
