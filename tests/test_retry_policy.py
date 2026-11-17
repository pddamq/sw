import pytest
import httpx
from datetime import datetime, timezone, timedelta
from email.utils import format_datetime
from types import SimpleNamespace as NS
from backend.model_retry import RetryPolicy, retry_delay, retry_after, retry_model_request
from backend.model_transport import stream_error, ModelClient
from backend.model_endpoint import client_options
from backend.config import Settings


def error(value):
    response = httpx.Response(429, request=httpx.Request('POST', 'https://fixture.invalid'),
                              headers={'Retry-After': value})
    return httpx.HTTPStatusError('fixture', request=response.request, response=response)


def test_retry_after_seconds_date_and_cap():
    policy = RetryPolicy(3, 1, 30, 0)
    assert retry_delay(error('12'), 0, policy) == 12
    assert 8 <= retry_after(error(format_datetime(datetime.now(timezone.utc) + timedelta(seconds=10)))) <= 10
    with pytest.raises(RuntimeError, match='Retry-After'):
        retry_delay(error('60'), 0, policy)
    assert retry_delay(error('invalid'), 1, policy) == 2


def test_jitter_is_bounded_and_never_preempts_server_delay(monkeypatch):
    monkeypatch.setattr('backend.model_retry.random.uniform', lambda a, b: b)
    assert retry_delay(error('0'), 0, RetryPolicy(5, 2, 10, .5)) == 3
    assert retry_delay(error('9'), 0, RetryPolicy(5, 2, 10, .5)) == 9
    assert retry_delay(error('0'), 4, RetryPolicy(5, 2, 10, .5)) == 10


async def test_zero_retries_and_configuration_errors_never_sleep():
    attempts = []
    async def operation():
        attempts.append(1)
        raise error('1')
    async def forbidden(*args):
        pytest.fail('Unexpected backoff')
    with pytest.raises(RuntimeError, match='0 次'):
        await retry_model_request(operation, forbidden, RetryPolicy(max_retries=0))
    assert attempts == [1]
    with pytest.raises(ValueError, match='fixture'):
        retry_delay(ValueError('fixture'), 0, RetryPolicy())


def test_native_stream_error_preserves_retry_headers():
    response = httpx.Response(200, request=httpx.Request('POST', 'https://fixture.invalid'), headers={'retry-after': '9'})
    with pytest.raises(Exception) as caught:
        stream_error(response, {'error': {'type': 'rate_limit_error'}})
    assert retry_after(caught.value) == 9


@pytest.mark.parametrize('protocol', ['chat', 'responses', 'claude'])
def test_configured_timeout_reaches_all_transports(protocol):
    config = Settings(protocol=protocol, model_idle_timeout_seconds=17)
    calls = []
    def factory(**kwargs):
        calls.append(kwargs)
        return NS()
    client = ModelClient(config, 'fixture', chat_factory=factory, chat_options=client_options)
    assert client.timeout == 17
    if protocol == 'chat':
        assert calls[0]['timeout'] == 17
    else:
        assert not calls
