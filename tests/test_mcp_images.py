import base64
import sys
from pathlib import Path

from backend.config import Settings
from backend.mcp_bridge import McpBridge


async def test_real_stdio_image_content_is_preserved():
    bridge = McpBridge()
    try:
        await bridge.connect(Settings(mcp_command=sys.executable,
            mcp_args=[str(Path(__file__).with_name('mcp_image_fixture.py'))]))
        result = await bridge.call('fixture_image', {})
        assert not result['is_error']
        assert result['image_count'] == 1
        assert result['images'][0]['mime_type'] == 'image/png'
        assert base64.b64decode(result['images'][0]['data']).startswith(b'\x89PNG')
    finally:
        await bridge.disconnect()
