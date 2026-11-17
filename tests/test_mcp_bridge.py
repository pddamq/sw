import asyncio
import sys
from pathlib import Path

from backend.config import Settings
from backend.mcp_bridge import McpBridge


async def test_mcp_owner_task_protocol_and_reconnect():
    bridge = McpBridge()
    settings = Settings(mcp_command=sys.executable,
                        mcp_args=[str(Path(__file__).with_name('mcp_fixture.py'))])
    try:
        info = await bridge.connect(settings)
        assert info['running'] and info['tools'] == 3
        status = await bridge.call('sw_status', {})
        assert status['data']['attached'] is False
        result = await bridge.call('fixture_echo', {'value': 'hello MCP'})
        assert result['content'] == 'hello MCP'
        assert not result['is_error']
        await bridge.disconnect()
        assert not bridge.running
        await bridge.connect(settings)
        result = await bridge.call('fixture_echo', {'value': 'reconnected'})
        assert result['content'] == 'reconnected'
    finally:
        await bridge.disconnect()


async def test_cancel_drains_inflight_operation():
    bridge = McpBridge()
    try:
        await bridge.connect(Settings(mcp_command=sys.executable,
            mcp_args=[str(Path(__file__).with_name('mcp_fixture.py'))]))
        task = asyncio.create_task(bridge.call('fixture_wait', {'seconds': 0.4}))
        await asyncio.sleep(0.1)
        task.cancel()
        await asyncio.sleep(0.1)
        task.cancel()
        await asyncio.sleep(0.05)
        assert not task.done(), 'Repeated cancellation must not release an in-flight MCP operation'
        await asyncio.gather(task, return_exceptions=True)
        assert (await bridge.call('fixture_echo', {'value': 'after cancel'}))['content'] == 'after cancel'
    finally:
        await bridge.disconnect()
