"""Deterministic MCP fixture. This is not a SOLIDWORKS substitute."""
from mcp.server.fastmcp import FastMCP

server = FastMCP('test-fixture-not-cad')


@server.tool()
def sw_status() -> str:
    return '{"attached":false,"note":"test fixture: no CAD execution"}'


@server.tool()
def fixture_echo(value: str) -> str:
    return value


@server.tool()
async def fixture_wait(seconds: float = 0.1) -> str:
    import asyncio
    await asyncio.sleep(seconds)
    return 'completed fixture wait'


if __name__ == '__main__':
    server.run(transport='stdio')
