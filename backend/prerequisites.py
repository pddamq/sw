"""Desktop dependency checks and explicit, user-approved WebView2 repair.

Inventory and payload validation never execute an installer. Repair is called only by
an interactive desktop confirmation, or by the separate installation wizard.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import time

from .config import ROOT
from .setup import windows_inventory

WEBVIEW_PAGE = 'https://developer.microsoft.com/microsoft-edge/webview2/'
BOOTSTRAPPER_NAME = 'MicrosoftEdgeWebview2Setup.exe'


class DesktopDependencyError(RuntimeError):
    pass


def bootstrapper_info(root: Path | None = None) -> dict:
    folder = (root if root is not None else ROOT) / 'resources' / 'prerequisites'
    path = folder / BOOTSTRAPPER_NAME
    manifest_path = folder / 'webview2-bootstrapper.json'
    invalid = {'available': False, 'path': str(path), 'requires_internet': True}
    try:
        if not path.is_file() or not manifest_path.is_file() or manifest_path.stat().st_size > 16000:
            return invalid
        if not 1024 <= path.stat().st_size <= 4 * 1024 * 1024:
            return invalid
        manifest = json.loads(manifest_path.read_text('utf-8'))
        signature = manifest.get('signature', {})
        if manifest.get('file') != BOOTSTRAPPER_NAME or signature.get('status') != 'Valid':
            return invalid
        if not re.search(r'(?:^|,\s*)CN=Microsoft Corporation(?:,|$)', signature.get('subject', '')):
            return invalid
        content = path.read_bytes()
        if not content.startswith(b'MZ') or len(content) != manifest.get('bytes'):
            return invalid
        if hashlib.sha256(content).hexdigest() != manifest.get('sha256'):
            return invalid
        return {'available': True, 'path': str(path), 'requires_internet': True,
                'sha256': manifest['sha256'], 'publisher': 'Microsoft Corporation'}
    except (OSError, ValueError, TypeError, AttributeError):
        return invalid


def startup_report() -> dict:
    from . import __version__
    import platform
    state = windows_inventory()
    bootstrap = bootstrapper_info()
    ready = bool(state.get('webview2') and (state.get('netfx_release') or 0) >= 528040)
    return {'app_version': __version__, 'platform': sys.platform, 'machine': platform.machine(),
            'windows_version': platform.version(), 'desktop_runtime_ready': ready,
            'webview2': state.get('webview2'), 'netfx_release': state.get('netfx_release'),
            'webview_bootstrapper': bootstrap,
            'cad_installed': bool(state.get('com_registered')),
            'note': 'Read-only runtime checks. No installer, executor, CAD process or model service was started.'}


def install_webview(root: Path | None = None, *, run=None, inventory=None, wait=None) -> dict:
    info = bootstrapper_info(root)
    if not info['available']:
        raise DesktopDependencyError('随包 WebView2 安装引导程序缺失或校验失败，请重新下载完整分发包。')
    runner = run if run is not None else subprocess.run
    inspect = inventory if inventory is not None else windows_inventory
    pause = wait if wait is not None else time.sleep
    if inspect().get('webview2'):
        return {'installed': True, 'already_present': True, 'restart_required': False}
    try:
        result = runner([info['path'], '/silent', '/install'], timeout=600, check=False,
                        env={key: value for key, value in os.environ.items() if key != 'FORMA_API_KEY'},
                        creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == 'win32' else 0)
    except subprocess.TimeoutExpired as exc:
        raise DesktopDependencyError('WebView2 安装等待超时，安装进程可能仍在后台继续。请稍后重新打开应用，勿连续重复安装。') from exc
    except OSError as exc:
        raise DesktopDependencyError('WebView2 安装程序启动失败，请检查当前用户权限，或运行完整安装包。') from exc
    # A nonzero exit may mean another installer completed the same prerequisite.
    for attempt in range(8):
        state = inspect()
        if state.get('webview2'):
            return {'installed': True, 'already_present': False, 'restart_required': False}
        if attempt < 7:
            pause(1)
    if result.returncode in {3010, 1641}:
        raise DesktopDependencyError('WebView2 安装要求重启 Windows，请重启后再次打开应用。')
    raise DesktopDependencyError(f'WebView2 尚未就绪，安装返回码 {result.returncode}。请检查网络或系统安装限制，再运行安装包重试；不会开始 CAD 操作。')


def ensure_desktop_runtime(*, interactive: bool = True, inventory=None, confirm=None, install=None) -> None:
    inspect = inventory if inventory is not None else windows_inventory
    if sys.platform != 'win32':
        return
    state = inspect()
    if (state.get('netfx_release') or 0) < 528040:
        raise DesktopDependencyError('桌面窗口需要 Windows .NET Framework 4.8。请通过 Windows 更新修复此系统组件后重试；无需安装 .NET SDK。')
    if state.get('webview2'):
        return
    if not interactive:
        raise DesktopDependencyError('缺少 Microsoft Edge WebView2 Runtime。测试模式不会自动安装，请先运行完整安装包。')
    info = bootstrapper_info()
    if not info['available']:
        raise DesktopDependencyError('缺少 WebView2，且随包安装引导程序不可用。请运行完整安装包或从 Microsoft 官网安装 Evergreen Runtime：' + WEBVIEW_PAGE)
    if confirm is None:
        import ctypes
        def confirm(message):
            return ctypes.windll.user32.MessageBoxW(None, message, 'Forma CAD — 安装桌面依赖', 0x24) == 6
    accepted = confirm('缺少 Microsoft Edge WebView2 桌面运行时。是否联网安装？\n\n'
                       '将运行随包校验的 Microsoft 安装引导程序，并从 Microsoft 下载运行时。'
                       '通常可按当前用户安装；网络或企业策略可能要求进一步处理。\n\n'
                       '点击“是”开始，点击“否”退出；不会启动 SOLIDWORKS 或修改模型。')
    if not accepted:
        raise DesktopDependencyError('已取消安装 WebView2。可稍后运行完整安装包，或再次打开应用进行安装。')
    repair = install if install is not None else install_webview
    repair()
    if not inspect().get('webview2'):
        raise DesktopDependencyError('WebView2 安装后仍未检出，请重新启动应用或检查 Windows 安装策略。')
