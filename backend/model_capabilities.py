"""Explicit per-model capability overrides; catalog names alone prove no capability."""
from typing import Literal
from urllib.parse import urlsplit
from pydantic import BaseModel, Field, StrictBool, model_validator

Effort = Literal['low', 'medium', 'high']


class ModelCapability(BaseModel):
    reasoning_efforts: list[Effort] | None = None
    vision: StrictBool | None = None
    context_window: int | None = Field(default=None, strict=True, gt=0)
    max_output_tokens: int | None = Field(default=None, strict=True, gt=0)

    @model_validator(mode='after')
    def validate_limits(self):
        if self.context_window and self.max_output_tokens and self.max_output_tokens >= self.context_window:
            raise ValueError('Model output limit must be smaller than its context window.')
        if self.reasoning_efforts is not None:
            self.reasoning_efforts = list(dict.fromkeys(self.reasoning_efforts))
        return self


def capability(config):
    value = config.model_capabilities.get(config.model)
    return value if isinstance(value, ModelCapability) else ModelCapability(**(value or {}))


def apply_capabilities(config):
    caps = capability(config)
    values = config.model_dump()
    for field in ('context_window', 'max_output_tokens'):
        if getattr(caps, field) is not None:
            values[field] = getattr(caps, field)
    if caps.vision is not None:
        values['vision_enabled'] = caps.vision
    if values['max_output_tokens'] >= values['context_window']:
        raise ValueError('Effective model output limit must be smaller than its context window.')
    # Service defaults and other models were validated when loaded. Revalidating
    # their inherited limits against this model's overrides would mix models.
    return config.model_copy(update={name: values[name] for name in
                                     ('context_window', 'max_output_tokens', 'vision_enabled')}, deep=True)


def validate_effort(config, effort):
    if effort is None:
        return
    caps = capability(config)
    if caps.reasoning_efforts is not None and effort not in caps.reasoning_efforts:
        raise ValueError('所选思考强度不在此模型配置的支持列表中，请选择默认或调整模型能力。')
    if (config.protocol == 'claude' and urlsplit(config.base_url).hostname == 'api.deepseek.com'
            and effort == 'medium'):
        raise ValueError('DeepSeek Messages 思考强度请选择 low 或 high，或使用默认值。')
