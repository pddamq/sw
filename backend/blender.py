"""Blender MCP preset and a tool scope that never admits native SolidWorks calls."""
from __future__ import annotations

from pathlib import Path

from .config import ROOT
from .blender_bundle import verify_bundle

BLENDER_PACKAGE = 'mcp-for-blender==2.2.7'
BLENDER_PORT = 9876


def bundle_folder():
    return ROOT / 'resources' / 'blender'


def bundled_command():
    return bundle_folder() / 'server' / 'forma-blender-mcp.exe'


def valid_bundled_args(args):
    return (isinstance(args, list) and len(args) == 4 and args[:3] == ['--host', '127.0.0.1', '--port']
            and isinstance(args[3], str) and args[3].isascii() and args[3].isdigit()
            and 1024 <= int(args[3]) <= 65535)


def blender_preset():
    available = bundled_command().is_file()
    return {'name': 'Blender', 'transport': 'stdio', 'command': str(bundled_command()),
            'args': ['--host', '127.0.0.1', '--port', str(BLENDER_PORT)],
            'cwd': '', 'url': '', 'enabled': True, 'bundled': 'blender', 'launcher_found': available,
            'bundle_available': available, 'package': BLENDER_PACKAGE, 'addon_port': BLENDER_PORT,
            'documentation_url': 'https://github.com/ahujasid/mcp-for-blender',
            'note': '应用自带 MCP 服务、Python 运行时和插件，无需 uv、命令行或 JSON。'
                    '点击一键启动，将在独立 Blender 窗口中自动加载插件并核对场景。'}


class BlenderToolCatalog:
    def __init__(self, extensions):
        self.extensions = extensions

    def connections(self):
        if self.extensions is None:
            return []
        result = []
        for server_id, connection in self.extensions.connections.items():
            names = {tool['name'] for tool in connection.tools}
            if (connection.running and 'get_scene_info' in names
                    and names & {'execute_blender_code', 'execute_code', 'get_object_info'}):
                result.append((server_id, connection))
        return result

    @property
    def tools(self):
        if self.extensions is None:
            return []
        prefixes = tuple('ext_' + server_id + '_' for server_id, _ in self.connections())
        return [tool for tool in self.extensions.tools if tool['name'].startswith(prefixes)]

    @property
    def running(self):
        return bool(self.connections())

    @property
    def baseline_names(self):
        return {self.extensions.tool_name(server_id, tool['name'])
                for server_id, connection in self.connections() for tool in connection.tools
                if tool['name'] in {'get_scene_info', 'get_addon_status'}}
