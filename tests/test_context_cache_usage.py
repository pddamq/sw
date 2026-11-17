"""Offline provider accounting regression tests."""
import pytest

from backend.config import Settings
from backend.model_transport import usage_value
from test_native_model_protocols import claude_rows, install_transport, native_client, sse


def test_claude_cache_is_input_but_responses_cache_is_already_included():
    usage = {'input_tokens': 20, 'output_tokens': 7,
             'cache_read_input_tokens': 100, 'cache_creation_input_tokens': 50}
    assert usage_value(usage, 'claude').prompt_tokens == 170
    assert usage_value(usage, 'responses').prompt_tokens == 20
    assert usage_value(usage, 'claude').completion_tokens == 7
    assert usage_value({'input_tokens': 0}, 'claude').prompt_tokens == 0
    assert usage_value({}, 'claude').prompt_tokens is None


@pytest.mark.parametrize('invalid', [-1, True, '100', None])
def test_invalid_cache_counts_do_not_become_reported_usage(invalid):
    assert usage_value({'input_tokens': 20, 'cache_read_input_tokens': invalid}, 'claude').prompt_tokens is None


async def test_claude_stream_retains_cached_input_through_final_usage(monkeypatch):
    rows = claude_rows(text='Done')
    rows[0]['message']['usage'].update(cache_read_input_tokens=100, cache_creation_input_tokens=50)
    install_transport(monkeypatch, lambda request: sse(rows))
    async with native_client(Settings(protocol='claude', base_url='https://provider.test')) as client:
        stream = await client.create(model='fixture', messages=[{'role': 'user', 'content': 'Hello'}],
                                     stream=True, max_tokens=100)
        async with stream:
            counts = [item.usage.prompt_tokens async for item in stream if item.usage is not None]
    assert counts == [165, 165]
