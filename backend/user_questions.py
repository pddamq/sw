"""Local user-question schemas; answers never grant CAD operation approval."""
from __future__ import annotations

from typing import Annotated

from jsonschema import Draft202012Validator
from pydantic import BaseModel, ConfigDict, Field, StrictBool, StrictStr

CLARIFY = {'type': 'function', 'function': {
    'name': 'request_clarification',
    'description': 'Ask the user through a local interactive card for essential missing information. '
                   'Call alone. Waits for their answer, then returns it so this task can continue. '
                   'If skipped, returns no answer: continue independent work or explain blocked steps; '
                   'do not invent missing information or repeat the same question. '
                   'Supports free text and optional single/multiple choice. Runs no CAD operation '
                   'and grants no approval. Use normal CAD tools and their UI confirmation for edit approval.',
    'parameters': {'type': 'object', 'properties': {
        'question': {'type': 'string', 'minLength': 1, 'maxLength': 2000, 'pattern': r'\S'},
        'reason': {'type': 'string', 'minLength': 1, 'maxLength': 1000, 'pattern': r'\S'},
        'options': {'type': 'array', 'minItems': 2, 'maxItems': 8, 'items': {
            'type': 'object', 'properties': {
                'id': {'type': 'string', 'pattern': '^[A-Za-z0-9_-]{1,64}$'},
                'label': {'type': 'string', 'minLength': 1, 'maxLength': 300, 'pattern': r'\S'}},
            'required': ['id', 'label'], 'additionalProperties': False}},
        'allow_multiple': {'type': 'boolean'}},
        'required': ['question', 'reason'], 'additionalProperties': False}}}


class QuestionAnswer(BaseModel):
    model_config = ConfigDict(extra='forbid')
    selected_option_ids: list[Annotated[StrictStr, Field(max_length=64)]] = Field(default_factory=list, max_length=8)
    text: Annotated[StrictStr, Field(max_length=4000)] = ''
    cancelled: StrictBool = False


def validate_question(arguments: dict) -> None:
    Draft202012Validator(CLARIFY['function']['parameters']).validate(arguments)
    ids = [option['id'] for option in arguments.get('options', [])]
    if len(ids) != len(set(ids)):
        raise ValueError('Question option IDs must be unique.')
    if arguments.get('allow_multiple') and not ids:
        raise ValueError('Multiple choice requires options.')


def validate_answer(question: dict, answer: QuestionAnswer) -> dict:
    """Validate submitted choices against this pending question, not model output."""
    selected = answer.selected_option_ids
    text = answer.text.strip()
    if answer.cancelled:
        if selected or text:
            raise ValueError('A cancelled question must not include an answer.')
        return {'cancelled': True, 'selected_option_ids': [], 'selected_options': [], 'text': ''}
    options = {option['id']: option['label'] for option in question.get('options', [])}
    if len(selected) != len(set(selected)):
        raise ValueError('Each option may be selected only once.')
    if any(option_id not in options for option_id in selected):
        raise ValueError('Selected option does not belong to this question.')
    if not question.get('allow_multiple', False) and len(selected) > 1:
        raise ValueError('This question allows only one selected option.')
    if not selected and not text:
        raise ValueError('Select an option or enter an answer.')
    return {'cancelled': False, 'selected_option_ids': list(selected),
            'selected_options': [{'id': option_id, 'label': options[option_id]} for option_id in selected],
            'text': text}
