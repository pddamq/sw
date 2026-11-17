"""Real MCP transports against loopback fixtures, never external services or SolidWorks."""
import asyncio
import socket
import sys
from pathlib import Path

import pytest

from backend.execution import ExecutionControl, ExecutionHalted
from backend.external_mcp import ExternalMcpHub
from backend.external_mcp_config import ServerDraft, ServerStore
from backend.storage import Database
from test_agent import FakeBridge

FIXTURE = Path(__file__).with_name('external_mcp_fixture.py')


@pytest.mark.parametrize('transport,mode,endpoint', [
    ('streamable_http', 'streamable-http', '/mcp'), ('sse', 'sse', '/sse'),
])
async def test_loopback_http_and_sse_protocols(tmp_path, transport, mode, endpoint):
    with socket.socket() as reservation:
        reservation.bind(('127.0.0.1', 0))
        port = reservation.getsockname()[1]
    process = await asyncio.create_subprocess_exec(sys.executable, str(FIXTURE), mode, str(port),
        stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL)
    db = Database(tmp_path / 'transport.db')
    store = ServerStore(db)
    hub = ExternalMcpHub(store, tmp_path)
    try:
        for _ in range(120):
            if process.returncode is not None:
                pytest.fail('Loopback MCP fixture exited before becoming ready')
            try:
                _, writer = await asyncio.open_connection('127.0.0.1', port)
                writer.close(); await writer.wait_closed()
                break
            except OSError:
                await asyncio.sleep(0.05)
        else:
            pytest.fail('Loopback fixture startup timed out')
        item = store.save(ServerDraft(name='loopback', transport=transport,
            url=f'http://127.0.0.1:{port}{endpoint}', headers={'X-Fixture-Token': 'local-header-123'}))
        await hub.connect(item['id'])
        name = hub.tool_name(item['id'], 'echo')
        result = await hub.call(name, {'value': 'loopback ' + transport})
        assert result['content'] == 'loopback ' + transport and not result['is_error']
        assert hub.public()[0]['tool_count'] == 3
    finally:
        await hub.close()
        if process.returncode is None:
            process.terminate()
        try:
            await asyncio.wait_for(process.wait(), 10)
        except asyncio.TimeoutError:
            process.kill(); await process.wait()


async def test_stdio_environment_credentials_are_not_in_results_or_api(tmp_path):
    db = Database(tmp_path / 'secrets.db')
    store = ServerStore(db)
    item = store.save(ServerDraft(name='secret fixture', command=sys.executable, args=[str(FIXTURE)],
        env={'FIXTURE_SECRET': 'fixture-confidential-847'}))
    hub = ExternalMcpHub(store, tmp_path)
    try:
        await hub.connect(item['id'])
        result = await hub.call(hub.tool_name(item['id'], 'configured_value'), {})
        assert 'fixture-confidential-847' not in result['content']
        assert '[redacted]' in result['content']
        assert 'fixture-confidential-847' not in str(hub.public())
    finally:
        await hub.close()


async def test_unknown_external_outcome_stops_and_keeps_checkpoint(tmp_path):
    db = Database(tmp_path / 'crash.db')
    store = ServerStore(db)
    item = store.save(ServerDraft(name='crash fixture', command=sys.executable, args=[str(FIXTURE)]))
    hub = ExternalMcpHub(store, tmp_path)
    try:
        await hub.connect(item['id'])
        run = db.create_run(db.create_session()['id'], 'Synthetic interruption')
        bridge = FakeBridge(); bridge.running = False
        async def emit(event):
            if event['type'] == 'approval_required':
                control.decide(event['approval']['id'], True)
        control = ExecutionControl(db, bridge, run['id'], emit)
        control.extensions = hub
        with pytest.raises(ExecutionHalted):
            await control.invoke(hub.tool_name(item['id'], 'crash'), {}, purpose='Terminate the synthetic fixture')
        assert db.checkpoints(run['id'])[-1]['status'] == 'unknown'
        assert bridge.calls == []
    finally:
        await hub.close()


async def test_failed_connection_keeps_configuration_without_exposing_secrets(tmp_path):
    db = Database(tmp_path / 'missing.db')
    store = ServerStore(db)
    item = store.save(ServerDraft(name='missing fixture', command=str(tmp_path / 'missing.exe'),
        env={'TOKEN': 'confidential-no-error-echo'}))
    hub = ExternalMcpHub(store, tmp_path)
    try:
        with pytest.raises(ValueError) as error:
            await hub.connect(item['id'])
        assert 'confidential-no-error-echo' not in str(error.value)
        assert len(store.list()) == 1 and not hub.running
    finally:
        await hub.close()
