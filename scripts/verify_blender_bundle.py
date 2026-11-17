"""Verify a frozen Blender MCP executable over stdio without requiring Blender or uvx."""
from __future__ import annotations
import argparse
import asyncio
import json
import os
from pathlib import Path
import socket
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ.setdefault('FORMA_DATA_DIR', str(ROOT / 'build' / 'blender-check-runtime'))
from backend.blender_bundle import verify_bundle
from backend.external_mcp import ExternalConnection


async def main(folder: Path):
    folder = folder.resolve()
    manifest = verify_bundle(folder)
    with socket.socket() as reservation:
        reservation.bind(('127.0.0.1', 0))
        port = reservation.getsockname()[1]
    connection = ExternalConnection({'id': 'blender-build-check', 'name': 'Bundled Blender MCP', 'transport': 'stdio',
        'command': str(folder / manifest['executable']), 'args': ['--host', '127.0.0.1', '--port', str(port)],
        'cwd': '', 'env': {}, 'headers': {}}, ROOT / 'build')
    try:
        await connection.open()
        names = {tool['name'] for tool in connection.tools}
        if not {'get_scene_info', 'execute_blender_code'}.issubset(names):
            raise RuntimeError('Bundled server is missing the actual Blender modeling tools')
        catalog = {'package': manifest['package'], 'version': manifest['version'],
                   'tools': sorted(names), 'tool_count': len(names), 'real_blender_scene_verified': False}
        (ROOT / 'build' / 'blender-protocol-check.json').write_text(json.dumps(catalog, indent=2), 'utf-8')
        print(f'Frozen Blender MCP protocol verified: {len(names)} tools, no uvx/Python installation needed.')
    finally:
        await connection.disconnect()


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--folder', type=Path, default=ROOT / 'resources' / 'blender')
    asyncio.run(main(parser.parse_args().folder))
