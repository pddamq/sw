import sys
from pathlib import Path

import httpx
import pytest

from backend.app import create_app
from backend.config import Settings, SettingsStore
from backend.mcp_bridge import McpBridge, launch_diagnostics
from backend.storage import Database


async def test_connection_config_change_requires_reconnect(tmp_path):
    config = SettingsStore(tmp_path / 'settings.json')
    config.update({'mcp_command': sys.executable,
                   'mcp_args': [str(Path(__file__).with_name('mcp_fixture.py'))]})
    bridge = McpBridge()
    app = create_app(Database(tmp_path / 'app.db'), config, bridge, token='fixture')
    try:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url='http://testserver',
                                     headers={'Authorization': 'Bearer fixture'}) as api:
            response = await api.post('/api/mcp/connect')
            assert response.status_code == 200
            assert response.json()['solidworks_connected'] is False
            health = (await api.get('/api/health')).json()
            assert health['mcp']['running'] and not health['mcp_needs_reconnect']
            assert not health['solidworks']['connected']
            await api.put('/api/settings', json={'mcp_cwd': str(tmp_path)})
            assert (await api.get('/api/health')).json()['mcp_needs_reconnect'] is True
            diagnostics = (await api.get('/api/diagnostics')).json()
            assert diagnostics['mcp_launch']['command_found']
    finally:
        await bridge.disconnect()


async def test_missing_executor_and_cwd_report_actionable_errors(tmp_path):
    bridge = McpBridge()
    with pytest.raises(ValueError, match='PATH'):
        await bridge.connect(Settings(mcp_command=str(tmp_path / 'missing.exe')))
    with pytest.raises(ValueError, match='工作目录'):
        await bridge.connect(Settings(mcp_command=sys.executable, mcp_cwd=str(tmp_path / 'missing')))
    assert not bridge.running
    assert launch_diagnostics(Settings(mcp_command=sys.executable))['command_found']
