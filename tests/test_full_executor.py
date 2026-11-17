from __future__ import annotations

import asyncio
import hashlib
import json
import os
from pathlib import Path
import sys

from jsonschema import Draft202012Validator
import pytest

from backend.agent import select_tools
from backend.config import Settings
from backend.mcp_bridge import McpBridge
from scripts.verify_full_executor import expected_tools

ROOT = Path(__file__).resolve().parents[1]


def test_pinned_full_catalog_and_vba_opt_in():
    names = expected_tools(ROOT / 'vendor' / 'solidworks-mcp' / 'src' / 'SolidWorksMcp.Server' / 'Tools')
    assert len(names) == 161
    assert {'sim_static', 'sim_run', 'sim_plot', 'sim_list', 'sim_delete', 'feature_recognize', 'vba_run'} <= names
    tools = [{'name': 'vba_run', 'description': 'Run VBA macro automation'}]
    assert select_tools(tools, 'VBA macro') == []
    assert select_tools(tools, 'VBA macro', allow_vba=True) == tools
    assert Settings().allow_vba is False


async def test_stdio_environment_is_explicit_and_secret_free(tmp_path, monkeypatch):
    import backend.mcp_bridge as module
    from contextlib import asynccontextmanager
    observed = []
    original = module.stdio_client
    @asynccontextmanager
    async def capture(params, **kwargs):
        observed.append(params.env)
        async with original(params, **kwargs) as streams:
            yield streams
    monkeypatch.setattr(module, 'stdio_client', capture)
    monkeypatch.setenv('FORMA_API_KEY', 'secret-should-not-reach-executor')
    monkeypatch.setenv('SWMCP_AUTO_LAUNCH', '1')
    monkeypatch.setenv('SWMCP_TOOLSETS', 'simulation')
    monkeypatch.setattr(module, 'DATA_DIR', tmp_path)
    bridge = McpBridge()
    settings = Settings(mcp_command=sys.executable, mcp_args=[str(ROOT / 'tests' / 'mcp_fixture.py')])
    try:
        for enabled in (False, True):
            settings.allow_vba = enabled
            await bridge.connect(settings)
            assert observed[-1]['SWMCP_VBA'] == ('on' if enabled else 'off')
            assert observed[-1]['SWMCP_AUTO_LAUNCH'] == '0'
            assert observed[-1]['SWMCP_TOOLSETS'] == 'all'
            assert observed[-1]['SWMCP_READ_ONLY'] == '0'
            assert observed[-1]['SWMCP_DATA_DIR'] == str(tmp_path / 'executor')
            assert 'FORMA_API_KEY' not in observed[-1]
            assert bridge.configuration['allow_vba'] is enabled
            await bridge.disconnect()
    finally:
        await bridge.disconnect()


@pytest.mark.skipif(sys.platform != 'win32', reason='Windows COM executor')
async def test_real_full_executor_matches_all_upstream_schemas_and_reconnects():
    exe = ROOT / 'resources' / 'mcp' / 'solidworks-mcp.exe'
    catalog_path = exe.parent / 'tool-catalog.json'
    if not exe.is_file() or not catalog_path.is_file():
        if os.environ.get('FORMA_REQUIRE_FULL_MCP') == '1':
            pytest.fail('A verified full executor is required for this release')
        pytest.skip('Build the full executor before this distribution-level regression check')
    catalog = json.loads(catalog_path.read_text('utf-8'))
    assert catalog['tool_count'] == 161 and catalog['excluded_tools'] == []
    for name, checksum in catalog['assembly_sha256'].items():
        assert hashlib.sha256((exe.parent / name).read_bytes()).hexdigest() == checksum
    expected = expected_tools(ROOT / 'vendor' / 'solidworks-mcp' / 'src' / 'SolidWorksMcp.Server' / 'Tools')
    bridge = McpBridge()
    try:
        for enabled in (False, True):
            result = await bridge.connect(Settings(mcp_command=str(exe), allow_vba=enabled))
            assert result['tools'] == 161
            assert {item['name'] for item in bridge.tools} == expected
            for item in bridge.tools:
                Draft202012Validator.check_schema(item['inputSchema'])
            status = await bridge.call('sw_status', {})
            assert not status['is_error'] and status['data']['attached'] is False
            assert status['data']['vbaEnabled'] is enabled
            if enabled:
                assert not status['data'].get('vbaNote')
            else:
                assert 'SWMCP_VBA=on' in status['data']['vbaNote']
            assert status['data']['recentOpsLimit'] >= 1
            assert status['data']['recentOpsTotal'] >= 0
            assert status['data']['recentOpsTruncated'] is False
            assert status['data'].get('operationLog', '').endswith('operations.jsonl')
            await bridge.disconnect()
    finally:
        await bridge.disconnect()
