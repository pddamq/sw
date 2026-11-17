"""Bounded retries for model transport only; never wrap CAD execution."""
from __future__ import annotations

import asyncio
import math
import random
from contextlib import aclosing, asynccontextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from typing import Awaitable, Callable, TypeVar

import httpx
from openai import APIConnectionError, APIStatusError

T = TypeVar('T')
RETRY_DELAYS = (1, 2, 4, 8, 16)
MAX_RETRIES = len(RETRY_DELAYS)


@dataclass(frozen=True)
class RetryPolicy:
    max_retries: int = 5
    initial_seconds: float = 1
    max_seconds: float = 30
    jitter: float = 0

    def __post_init__(self):
        if (isinstance(self.max_retries, bool) or not isinstance(self.max_retries, int)
                or not 0 <= self.max_retries <= 10):
            raise ValueError('Invalid retry count')
        if (not all(math.isfinite(v) for v in (self.initial_seconds, self.max_seconds, self.jitter))
                or not 0 < self.initial_seconds <= self.max_seconds <= 300 or not 0 <= self.jitter <= 1):
            raise ValueError('Invalid retry delay bounds')

    @classmethod
    def from_config(cls, config):
        return cls(config.model_max_retries, config.model_retry_initial_seconds,
                   config.model_retry_max_seconds, config.model_retry_jitter)


class ModelStreamInterrupted(RuntimeError):
    """The model stream ended without a complete finish marker."""


def retryable_model_error(error: Exception) -> bool:
    if isinstance(error, (ModelStreamInterrupted, APIConnectionError, httpx.TransportError, TimeoutError)):
        return True
    if isinstance(error, (APIStatusError, httpx.HTTPStatusError)):
        status = error.status_code if isinstance(error, APIStatusError) else error.response.status_code
        return status in {408, 429} or 500 <= status < 600
    return False


def retry_after(error):
    response = getattr(error, 'response', None)
    value = response.headers.get('retry-after') if response is not None else None
    if value is None:
        return None
    try:
        delay = float(value)
    except (TypeError, ValueError):
        try:
            stamp = parsedate_to_datetime(value)
            if stamp.tzinfo is None:
                stamp = stamp.replace(tzinfo=timezone.utc)
            delay = (stamp - datetime.now(timezone.utc)).total_seconds()
        except (TypeError, ValueError, OverflowError):
            return None
    return max(0, delay) if math.isfinite(delay) else None


def retry_delay(error, attempt, policy):
    if not retryable_model_error(error):
        raise error
    if attempt >= policy.max_retries:
        raise RuntimeError(f'模型连接在自动重试 {policy.max_retries} 次后仍中断，本次任务已停止；此前完成的工具操作保留。') from error
    server_delay = retry_after(error)
    if server_delay is not None and server_delay > policy.max_seconds:
        raise RuntimeError('服务要求的 Retry-After 超过最大重试间隔，本次任务停止，未提前重试。') from error
    base = min(policy.max_seconds, policy.initial_seconds * 2 ** attempt)
    delay = min(policy.max_seconds, base * (1 + random.uniform(0, policy.jitter))) if policy.jitter else base
    return max(delay, server_delay or 0)


async def wait_before_retry(delay: float) -> None:
    await asyncio.sleep(delay)


async def retry_model_request(operation: Callable[[], Awaitable[T]],
                              on_retry: Callable[[int, float], Awaitable[None]],
                              policy: RetryPolicy | None = None) -> T:
    policy = policy or RetryPolicy()
    for attempt in range(policy.max_retries + 1):
        try:
            return await operation()
        except Exception as exc:
            delay = retry_delay(exc, attempt, policy)
        await on_retry(attempt + 1, delay)
        await wait_before_retry(delay)
    raise AssertionError('Unreachable retry state')


@asynccontextmanager
async def retrying_stream(create_stream, on_retry, policy: RetryPolicy | None = None, *, usage_grace_seconds: float = 0):
    policy = policy or RetryPolicy()
    async def chunks():
        for attempt in range(policy.max_retries + 1):
            terminal = False
            try:
                stream = await create_stream()
                async with stream:
                    iterator = stream.__aiter__()
                    async for chunk in iterator:
                        if any(choice.finish_reason in {'stop', 'tool_calls', 'length', 'content_filter', 'incomplete'}
                               for choice in chunk.choices):
                            terminal = True
                        yield chunk
                        if terminal:
                            # Chat usage can follow the finish marker. Never wait indefinitely
                            # for EOF or admit additional content/calls after settlement.
                            if usage_grace_seconds > 0:
                                async def usage_tail():
                                    async for tail in iterator:
                                        if not tail.choices and getattr(tail, 'usage', None) is not None:
                                            return tail
                                    return None
                                try:
                                    tail = await asyncio.wait_for(usage_tail(), usage_grace_seconds)
                                except Exception:
                                    tail = None
                                if tail is not None:
                                    yield tail
                            return
                raise ModelStreamInterrupted('Model stream ended without a finish marker')
            except Exception as exc:
                if terminal and retryable_model_error(exc):
                    return
                delay = retry_delay(exc, attempt, policy)
            await on_retry(attempt + 1, delay)
            await wait_before_retry(delay)
    async with aclosing(chunks()) as iterator:
        yield iterator
