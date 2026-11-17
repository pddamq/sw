"""Loopback-only MCP transport fixture. Never launches CAD or accesses model services."""
import os
import sys

from mcp.server.fastmcp import FastMCP

mode = sys.argv[1] if len(sys.argv) > 1 else 'stdio'
port = int(sys.argv[2]) if len(sys.argv) > 2 else 8000
server = FastMCP('forma-extension-test', host='127.0.0.1', port=port, log_level='ERROR')


@server.tool()
def echo(value: str) -> str:
    return value


@server.tool()
def configured_value() -> str:
    return os.environ.get('FIXTURE_SECRET', 'not configured')


@server.tool()
def crash() -> str:
    os._exit(2)


if __name__ == '__main__':
    server.run(transport=mode)
