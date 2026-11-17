from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys

from .config import ROOT, SettingsStore
from .mcp_bridge import launch_diagnostics

INTEROP_NAMES = ('sldworks', 'swconst', 'cosworks', 'fworks', 'sldcostingapi')


def dotnet_inventory() -> dict:
    command = shutil.which('dotnet')
    result = {'command': command, 'runtimes': [], 'sdks': [], 'detail': ''}
    if not command:
        result['detail'] = '未发现 dotnet；自包含 MCP 通常不依赖系统 .NET，DLL 方式需要匹配运行时。'
        return result
    for argument, key in [('--list-runtimes', 'runtimes'), ('--list-sdks', 'sdks')]:
        try:
            response = subprocess.run([command, argument], capture_output=True, text=True,
                encoding='utf-8', errors='replace', timeout=5,
                creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == 'win32' else 0)
            if response.returncode:
                result['detail'] = 'dotnet 环境读取失败，请检查本机 .NET 安装。'
                continue
            for line in response.stdout[:40000].splitlines():
                if key == 'runtimes':
                    match = re.match(r'^(\S+)\s+(\d+\.\d+\.\d+[^\s]*)\s+\[', line)
                    if match:
                        result[key].append({'name': match[1], 'version': match[2]})
                else:
                    match = re.match(r'^(\d+\.\d+\.\d+[^\s]*)\s+\[', line)
                    if match:
                        result[key].append(match[1])
        except (OSError, subprocess.TimeoutExpired):
            result['detail'] = '读取 .NET 环境超时或失败。'
    return result


def webview_version(value) -> tuple[int, ...] | None:
    if not isinstance(value, str) or not re.fullmatch(r'\d+\.\d+\.\d+\.\d+', value):
        return None
    version = tuple(int(part) for part in value.split('.'))
    return version if version[0] >= 86 else None


def windows_inventory() -> dict:
    info = {'solidworks': [], 'com_registered': False, 'webview2': None, 'netfx_release': None}
    if sys.platform != 'win32':
        return info
    import winreg
    for version in range(2022, 2028):
        try:
            with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, f'SOFTWARE\\SolidWorks\\SOLIDWORKS {version}\\Setup',
                                0, winreg.KEY_READ | winreg.KEY_WOW64_64KEY) as key:
                folder = Path(winreg.QueryValueEx(key, 'SolidWorks Folder')[0])
                missing = [f'SolidWorks.Interop.{name}.dll' for name in INTEROP_NAMES
                           if not (folder / 'api' / 'redist' / f'SolidWorks.Interop.{name}.dll').is_file()]
                info['solidworks'].append({'version': version, 'path': str(folder), 'missing_interop': missing})
        except OSError:
            pass
    try:
        with winreg.OpenKey(winreg.HKEY_CLASSES_ROOT, r'SldWorks.Application\CLSID'):
            info['com_registered'] = True
    except OSError:
        pass
    try:
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r'SOFTWARE\Microsoft\NET Framework Setup\NDP\v4\Full',
                            0, winreg.KEY_READ | winreg.KEY_WOW64_64KEY) as key:
            info['netfx_release'] = winreg.QueryValueEx(key, 'Release')[0]
    except OSError:
        pass
    webview = r'Software\Microsoft\EdgeUpdate\Clients\{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}'
    best_version = (0, 0, 0, 0)
    for hive in (winreg.HKEY_CURRENT_USER, winreg.HKEY_LOCAL_MACHINE):
        for view in (winreg.KEY_WOW64_32KEY, winreg.KEY_WOW64_64KEY):
            try:
                with winreg.OpenKey(hive, webview, 0, winreg.KEY_READ | view) as key:
                    value = winreg.QueryValueEx(key, 'pv')[0]
                    parsed = webview_version(value)
                    if parsed is not None and parsed > best_version:
                        info['webview2'] = value
                        best_version = parsed
            except OSError:
                continue
    return info


def runtime_requirements(path: Path, dotnet: dict) -> tuple[bool, str, str]:
    if path.name.lower() == 'forma-mcp.exe':
        config = path.with_name(path.name + '.config')
        try:
            from xml.etree import ElementTree
            if config.stat().st_size > 64000:
                raise ValueError('Runtime config is too large')
            runtime = ElementTree.fromstring(config.read_text('utf-8-sig')).find('startup/supportedRuntime')
            if runtime is None or runtime.get('sku') != '.NETFramework,Version=v4.8':
                raise ValueError('Unexpected runtime target')
        except (OSError, ValueError, ElementTree.ParseError):
            return False, '基础执行器缺少有效的 .NET Framework 4.8 配置，请重新构建。', 'invalid'
        available = sys.platform == 'win32' and (windows_inventory()['netfx_release'] or 0) >= 528040
        return (available,
                '内置基础版，18 个工具；依赖 .NET Framework 4.8，无需 .NET 10、SDK 或 interop DLL。仍需真实 SW 连接验证。' if available else
                '内置基础版需要 Windows x64 和 .NET Framework 4.8。', 'netfx-4.8')
    config = path.with_name(path.stem + '.runtimeconfig.json')
    requirements = []
    mode = 'unknown'
    if config.is_file():
        try:
            if config.stat().st_size > 64000:
                raise ValueError('Runtime config is too large')
            options = json.loads(config.read_text('utf-8-sig')).get('runtimeOptions', {})
            requirements = options.get('frameworks', [])
            if options.get('framework'):
                requirements = [options['framework']]
            mode = 'framework-dependent' if requirements else ('self-contained' if options.get('includedFrameworks') else 'unknown')
        except (OSError, ValueError, TypeError, AttributeError):
            return False, '运行时配置读取失败，请重新发布此执行器。', 'invalid'
    if not isinstance(requirements, list) or len(requirements) > 8:
        return False, '运行时配置的 framework 列表无效。', 'invalid'
    for framework in requirements:
        if not isinstance(framework, dict) or not isinstance(framework.get('name'), str) or not isinstance(framework.get('version'), str):
            return False, '运行时配置的 framework 字段无效。', 'invalid'
    if path.suffix.lower() == '.dll' and not dotnet['command']:
        return False, 'DLL 方式需要 dotnet 主机和匹配的 .NET 运行时。', mode
    for framework in requirements:
        name = framework.get('name', '')
        version = framework.get('version', '').split('.')[:2]
        required = re.match(r'^(\d+)\.(\d+)\.(\d+)', framework['version'])
        if not required or not framework['name']:
            return False, '运行时配置缺少有效名称或版本。', 'invalid'
        minimum = tuple(int(part) for part in required.groups())
        def runtime_matches(runtime):
            installed = re.match(r'^(\d+)\.(\d+)\.(\d+)', runtime['version'])
            if not installed or runtime['name'] != name:
                return False
            actual = tuple(int(part) for part in installed.groups())
            return actual[:2] == minimum[:2] and actual >= minimum
        matches = any(runtime_matches(runtime) for runtime in dotnet['runtimes'])
        if not matches:
            return False, f'缺少匹配运行时：{name} {".".join(version)}。', mode
    detail = '发现自包含执行器；仍需连接验证。' if mode == 'self-contained' else '发现程序；这里只做静态检查，仍需连接验证。'
    return True, detail, mode


def discover_executors(root: Path = ROOT, local: Path | None = None, dotnet: dict | None = None) -> list[dict]:
    local = local if local is not None else Path(os.environ.get('LOCALAPPDATA', Path.home()))
    dotnet = dotnet if dotnet is not None else dotnet_inventory()
    paths: list[tuple[Path, str]] = [
        (root / 'resources' / 'builtin-mcp' / 'forma-mcp.exe', '内置基础执行器（18 工具）'),
    ]
    for folder, source in [(root / 'resources' / 'mcp', '内置发布目录'),
                            (Path(sys.executable).parent / 'mcp', '程序旁执行器目录')]:
        for name in ('solidworks-mcp.exe', 'solidworks-mcp.dll'):
            paths.append((folder / name, source))
    versions = local / 'solidworks-mcp' / 'bin'
    if versions.is_dir():
        try:
            folders = sorted((p for p in versions.iterdir() if p.is_dir()), key=lambda p: p.name, reverse=True)[:32]
            for folder in [versions, *folders]:
                for name in ('solidworks-mcp.exe', 'solidworks-mcp.dll'):
                    paths.append((folder / name, '上游本机安装目录'))
        except OSError:
            pass
    for name in ('solidworks-mcp.exe', 'solidworks-mcp'):
        command = shutil.which(name)
        if command:
            paths.append((Path(command), '系统 PATH'))
    results, seen = [], set()
    for path, source in paths:
        try:
            if not path.is_file():
                continue
            path = path.resolve()
        except OSError:
            continue
        identity = os.path.normcase(str(path))
        if identity in seen:
            continue
        seen.add(identity)
        usable, detail, mode = runtime_requirements(path, dotnet)
        command = str(dotnet['command'] or 'dotnet') if path.suffix.lower() == '.dll' else str(path)
        args = [str(path)] if path.suffix.lower() == '.dll' else []
        candidate_id = hashlib.sha256(identity.encode('utf-8')).hexdigest()[:24]
        results.append({'id': candidate_id, 'name': path.parent.name + ' / ' + path.name,
                        'command': command, 'args': args, 'cwd': str(path.parent), 'source': source,
                        'usable': usable, 'runtime_mode': mode, 'detail': detail})
    return results


def setup_report(settings: SettingsStore, bridge) -> dict:
    from .prerequisites import bootstrapper_info
    bootstrapper = bootstrapper_info()
    dotnet = dotnet_inventory()
    windows = windows_inventory()
    executors = discover_executors(dotnet=dotnet)
    from .model_profiles import ProfileStore
    try:
        model_ready = bool(ProfileStore(settings).resolve()[1])
    except ValueError:
        model_ready = False
    launch = launch_diagnostics(settings.value)
    basic_configured = Path(settings.value.mcp_command).name.lower() == 'forma-mcp.exe'
    checks = [
        {'id': 'model', 'title': '模型服务', 'status': 'configured' if model_ready else 'missing',
         'detail': '已保存模型配置，建议测试真实接口。' if model_ready else '请填写 Base URL、模型 ID 和 API Key。'},
        {'id': 'executor', 'title': 'C# MCP 执行器（内置基础版）' if basic_configured else 'C# MCP 执行器', 'status': 'connected' if bridge.running else ('configured' if launch['command_found'] and launch['working_directory_exists'] else 'missing'),
         'detail': f'已发现 {len(bridge.tools)} 个工具。' if bridge.running else ('已找到配置的启动程序，尚待连接。' if launch['command_found'] and launch['working_directory_exists'] else ('配置的工作目录不存在，请更正或留空。' if not launch['working_directory_exists'] else '选择本机执行器或手动填写程序路径。'))},
        {'id': 'cad', 'title': 'SOLIDWORKS', 'status': 'connected' if bridge.sw_status.get('attached') and bridge.running else ('installed' if windows['com_registered'] else 'missing'),
         'detail': '当前已连接。' if bridge.sw_status.get('attached') and bridge.running else ('已注册 COM；请启动 SW 并连接。' if windows['com_registered'] else '未发现 SW COM 注册；真实建模需要本机安装。')},
        {'id': 'webview', 'title': 'WebView2 桌面运行时', 'status': 'installed' if windows['webview2'] else 'unknown',
         'detail': str(windows['webview2'] or '注册表未检出；浏览器模式仍可使用。')},
        {'id': 'netfx', 'title': '.NET Framework 4.8', 'status': 'installed' if (windows['netfx_release'] or 0) >= 528040 else 'unknown',
         'detail': '桌面外壳依赖，与 C# MCP 的 .NET 10 运行时分开检查。'},
    ]
    source = ROOT / 'vendor' / 'solidworks-mcp' / 'src' / 'SolidWorksMcp.Server' / 'SolidWorksMcp.Server.csproj'
    sdk_ready = any(v.split('.')[0].isdigit() and int(v.split('.')[0]) >= 10 for v in dotnet['sdks'])
    interop_ready = any(not item['missing_interop'] for item in windows['solidworks'])
    return {'checks': checks, 'executors': executors, 'windows': windows, 'dotnet': dotnet,
            'bundled_full_available': (ROOT / 'resources' / 'mcp' / 'solidworks-mcp.exe').is_file(),
            'webview_repair': bootstrapper,
            'tool_access': {'listed_tools': len(bridge.tools), 'vba_enabled': bool(bridge.running and bridge.sw_status.get('vbaEnabled')),
                            'vba_reason': (bridge.sw_status.get('vbaNote') if bridge.running else '执行器未连接，无法确认 VBA 状态。'),
                            'note': '完整工具目录为 161 项；VBA 默认关闭，Simulation 与 FeatureWorks 需要本机模块和相应许可。'},
            'configuration_warnings': settings.public()['configuration_warnings'],
            'executor_kind': settings.public()['executor_kind'],
            'build': {'source_available': source.is_file(), 'sdk_available': sdk_ready, 'interop_available': interop_ready,
                      'prerequisites_ready': source.is_file() and sdk_ready and interop_ready,
                      'command': 'powershell -ExecutionPolicy Bypass -File scripts\\build-mcp.ps1 -SolidWorksDir "SW_INSTALL_DIR"',
                      'builtin_command': 'powershell -ExecutionPolicy Bypass -File scripts\\build-builtin-mcp.ps1'},
            'compatibility': 'SW2023 尚待真机验证；上游报告的基线为 SW2025 SP1.2。',
            'note': '本页只读扫描已知目录、注册表与 dotnet 清单；发现文件不代表已经验证执行器或建模能力。'}
