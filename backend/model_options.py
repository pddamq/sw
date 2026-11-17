"""Shared per-service limits and optional HTTP identity."""
from pydantic import BaseModel, Field, field_validator, model_validator
from .model_capabilities import ModelCapability

DEFAULT_CONTEXT_WINDOW = 1_000_000
DEFAULT_MAX_OUTPUT_TOKENS = 256_000
ADVANCED_FIELDS = ('context_window', 'max_output_tokens', 'user_agent', 'model_capabilities')


def stored_model_options(payload: dict) -> dict:
    """Preserve legacy inherited 8192 for saved small-context routes only."""
    values = dict(payload)
    output = values.get('max_output_tokens')
    if output is None or (isinstance(output, str) and not output.strip()):
        windows = [values.get('context_window')]
        capabilities = values.get('model_capabilities') or {}
        if isinstance(capabilities, dict):
            windows += [cap.get('context_window') for cap in capabilities.values()
                        if isinstance(cap, dict) and cap.get('max_output_tokens') is None]
        if any(type(window) is int and 8192 < window <= DEFAULT_MAX_OUTPUT_TOKENS for window in windows):
            values['max_output_tokens'] = 8192
    return values


class ModelOptions(BaseModel):
    model_capabilities: dict[str, ModelCapability] = Field(default_factory=dict, max_length=200)
    context_window: int = Field(default=DEFAULT_CONTEXT_WINDOW, strict=True, gt=0)
    max_output_tokens: int = Field(default=DEFAULT_MAX_OUTPUT_TOKENS, strict=True, gt=0)
    user_agent: str = Field(default='', max_length=1024)

    @field_validator('context_window', 'max_output_tokens', mode='before')
    @classmethod
    def default_blank_limit(cls, value, info):
        if value is None or (isinstance(value, str) and not value.strip()):
            return DEFAULT_CONTEXT_WINDOW if info.field_name == 'context_window' else DEFAULT_MAX_OUTPUT_TOKENS
        return value

    @field_validator('user_agent', mode='before')
    @classmethod
    def default_blank_agent(cls, value):
        return '' if value is None else value

    @field_validator('user_agent')
    @classmethod
    def validate_user_agent(cls, value):
        # Reject header separators before trimming; never permit extra injected headers.
        if any(ord(char) < 32 or ord(char) > 126 for char in value):
            raise ValueError('User-Agent must contain printable ASCII characters on a single line.')
        return value.strip()

    @model_validator(mode='after')
    def validate_limits(self):
        if self.max_output_tokens >= self.context_window:
            raise ValueError('Maximum output tokens must be smaller than the context window, leaving room for input.')
        for caps in self.model_capabilities.values():
            if (caps.max_output_tokens or self.max_output_tokens) >= (caps.context_window or self.context_window):
                raise ValueError('Per-model output tokens must be smaller than its effective context window.')
        return self

    def advanced_values(self):
        values = self.model_dump()
        return {name: values[name] for name in ADVANCED_FIELDS}
