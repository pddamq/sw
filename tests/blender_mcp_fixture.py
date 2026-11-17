"""Protocol fixture, not a Blender runtime and never executes submitted Python."""
import json
from mcp.server.fastmcp import FastMCP

server = FastMCP('synthetic-blender-protocol-fixture')
objects = []


@server.tool()
def get_addon_status() -> str:
    return json.dumps({'fixture': True, 'note': 'No real Blender installed'})


@server.tool()
def get_scene_info() -> str:
    return json.dumps({'fixture': True, 'objects': objects, 'unit_scale': 1})


@server.tool()
def execute_blender_code(code: str) -> str:
    objects.append({'name': 'FixtureCube', 'dimensions': [0.1, 0.1, 0.1]})
    return json.dumps({'fixture': True, 'note': 'Code recorded, never executed', 'code': code})


if __name__ == '__main__':
    server.run(transport='stdio')
