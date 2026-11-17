"""One-click Blender GUI startup using only application-owned resources."""
from __future__ import annotations
import asyncio
import json
import os
from pathlib import Path
import shutil
import socket
import subprocess
import sys
import uuid

from mcp.client.stdio import get_default_environment
from .blender import blender_preset, bundle_folder, bundled_command
from .blender_bundle import verify_bundle
from .external_mcp_config import ServerDraft


def blender_installations(saved_path: str = '') -> list[dict]:
    candidates = []
    if saved_path:
        candidates.append(Path(saved_path))
    command = shutil.which('blender')
    if command:
        candidates.append(Path(command))
    for base in (Path(os.environ.get('ProgramFiles', 'C:/Program Files')) / 'Blender Foundation',
                 Path(os.environ.get('LOCALAPPDATA', str(Path.home()))) / 'Programs' / 'Blender Foundation'):
        if base.is_dir():
            candidates.extend(base.glob('*/blender.exe'))
    if sys.platform == 'win32':
        import winreg
        for hive in (winreg.HKEY_CURRENT_USER, winreg.HKEY_LOCAL_MACHINE):
            for view in (winreg.KEY_WOW64_64KEY, winreg.KEY_WOW64_32KEY):
                try:
                    with winreg.OpenKey(hive, r'SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall', 0, winreg.KEY_READ | view) as root:
                        for index in range(min(winreg.QueryInfoKey(root)[0], 4096)):
                            try:
                                with winreg.OpenKey(root, winreg.EnumKey(root, index)) as key:
                                    name = str(winreg.QueryValueEx(key, 'DisplayName')[0])
                                    if not name.lower().startswith('blender'):
                                        continue
                                    try:
                                        candidates.append(Path(winreg.QueryValueEx(key, 'InstallLocation')[0]) / 'blender.exe')
                                    except OSError:
                                        icon = str(winreg.QueryValueEx(key, 'DisplayIcon')[0]).split(',')[0].strip('"')
                                        candidates.append(Path(icon))
                            except (OSError, ValueError, TypeError):
                                continue
                except OSError:
                    pass
    seen, result = set(), []
    for path in candidates:
        try:
            path = path.resolve()
            if path.is_file() and path.name.lower() == 'blender.exe' and str(path).casefold() not in seen:
                seen.add(str(path).casefold())
                result.append({'path': str(path), 'label': path.parent.name})
        except OSError:
            continue
    return result


class BlenderLauncher:
    def __init__(self, store, hub, data_dir):
        self.store, self.hub = store, hub
        self.data_dir = Path(data_dir)
        self.path_file = self.data_dir / 'blender-launch.json'
        self.process = None
        self.port = None
        self.tag = None
        self.server_id = None
        self.ready = False
        self.lock = asyncio.Lock()

    def saved_path(self):
        try:
            value = json.loads(self.path_file.read_text('utf-8'))
            result = value.get('executable', '') if isinstance(value, dict) else ''
            return result if isinstance(result, str) else ''
        except (OSError, ValueError):
            return ''

    def status(self):
        running = self.process is not None and self.process.poll() is None
        connection = self.hub.connections.get(self.server_id)
        return {'bundle_available': bundled_command().is_file(),
                'installations': blender_installations(self.saved_path()),
                'owned_window_running': running,
                'scene_ready': bool(self.ready and running and connection and connection.running),
                'server_id': self.server_id,
                'note': '一键打开独立 Blender 窗口、加载随包插件并连接，不改动现有 Blender 文档和插件配置。'}

    async def _wait_listener(self, timeout=45):
        loop = asyncio.get_running_loop()
        deadline = loop.time() + timeout
        while loop.time() < deadline:
            if self.process.poll() is not None:
                raise ValueError('Blender 启动提前退出，请检查 Blender 版本、显卡驱动及 blender-start.log。')
            try:
                reader, writer = await asyncio.wait_for(asyncio.open_connection('127.0.0.1', self.port), 0.5)
                writer.close()
                await writer.wait_closed()
                return
            except (OSError, asyncio.TimeoutError):
                await asyncio.sleep(0.25)
        raise ValueError('Blender 插件启动尚未完成，请检查新窗口或 blender-start.log 后重试；已有窗口保持。')

    async def start(self, executable: str | None = None, server_id: str | None = None):
        async with self.lock:
            await asyncio.to_thread(verify_bundle, bundle_folder())
            if server_id:
                config = self.store.get(server_id)
                if config.get('bundled') != 'blender':
                    raise ValueError('此服务不是内置 Blender MCP。')
            running = self.process is not None and self.process.poll() is None
            if self.ready and running and self.server_id == (server_id or self.server_id):
                connection = self.hub.connections.get(self.server_id)
                if connection and connection.running:
                    return {'connected': True, 'server_id': self.server_id, 'scene_ready': True,
                            'message': '继续使用已连接的 Blender 窗口。', 'servers': self.hub.public()}
            self.ready = False
            if not running:
                choices = await asyncio.to_thread(blender_installations, executable or self.saved_path())
                if executable:
                    target = Path(executable).resolve()
                    if not target.is_file() or target.name.lower() != 'blender.exe':
                        raise ValueError('请选择已安装的 Blender.exe 文件。')
                elif choices:
                    target = Path(choices[0]['path'])
                else:
                    raise ValueError('未发现已安装的 Blender。安装 Blender 后重试；便携版可在高级选项中选择 Blender.exe。')
                with socket.socket() as reservation:
                    reservation.bind(('127.0.0.1', 0))
                    self.port = reservation.getsockname()[1]
                self.tag = 'Forma_' + uuid.uuid4().hex
                env = {**get_default_environment(), 'FORMA_BLENDER_PORT': str(self.port), 'FORMA_BLENDER_TAG': self.tag}
                self.data_dir.mkdir(parents=True, exist_ok=True)
                with (self.data_dir / 'blender-start.log').open('ab') as log:
                    self.process = subprocess.Popen([str(target), '--factory-startup', '--disable-autoexec',
                        '--python', str(bundle_folder() / 'forma_start.py')], env=env,
                        stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT,
                        creationflags=subprocess.CREATE_NEW_PROCESS_GROUP if sys.platform == 'win32' else 0)
                # Store only the explicitly selected/detected executable, never credentials or scene data.
                self.path_file.write_text(json.dumps({'executable': str(target)}), 'utf-8')
            await self._wait_listener()
            existing = next((item for item in self.store.list() if item.get('bundled') == 'blender'), None)
            preset = blender_preset()
            item = self.store.save(ServerDraft(id=server_id or (existing['id'] if existing else None),
                name='Blender', transport='stdio', command=preset['command'],
                args=['--host', '127.0.0.1', '--port', str(self.port)], bundled='blender', enabled=True))
            self.server_id = item['id']
            try:
                await self.hub.disconnect(self.server_id)
                await self.hub.connect(self.server_id)
                name = self.hub.tool_name(self.server_id, 'get_scene_info')
                result = await self.hub.call(name, {})
                text = result.get('content', '')
                if result.get('is_error') or self.tag not in text:
                    raise ValueError('Blender 场景身份核对失败，尚未连接到本次启动的场景，请检查窗口和插件。')
            except BaseException:
                await self.hub.disconnect(self.server_id)
                raise
            self.ready = True
            return {'connected': True, 'server_id': self.server_id, 'scene_ready': True,
                    'message': 'Blender 已启动，插件已自动加载，真实场景已核对。', 'servers': self.hub.public()}
