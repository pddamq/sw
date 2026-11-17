"""Recognize explicit provider context overflow, never generic 400/timeout failures."""

from openai import APIStatusError
import httpx

CODES = {'context_length_exceeded', 'context_window_exceeded', 'prompt_too_long', 'input_too_long'}


def is_context_overflow(error):
    if not isinstance(error, (APIStatusError, httpx.HTTPStatusError)):
        return False
    status = getattr(error, 'status_code', None) or error.response.status_code
    if status not in {400, 413, 422}:
        return False
    body = getattr(error, 'body', None)
    if body is None:
        try:
            body = error.response.json()
        except (ValueError, RuntimeError):
            return False
    if not isinstance(body, dict):
        return False
    body_response = body.get('response')
    nested = body_response.get('error') if isinstance(body_response, dict) else None
    detail = body.get('error') or nested or body
    if not isinstance(detail, dict):
        return False
    if any(isinstance(detail.get(field), str) and detail[field] in CODES for field in ('code', 'type')):
        return True
    message = str(detail.get('message', '')).lower()
    return (detail.get('type') == 'invalid_request_error' and
            any(phrase in message for phrase in ('prompt is too long', 'prompt too long',
                                                 'maximum context length', 'context window exceeded')))
