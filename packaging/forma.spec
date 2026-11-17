# -*- mode: python ; coding: utf-8 -*-
import hashlib
from pathlib import Path
import os
import json
from PyInstaller.utils.hooks import collect_all
import sys
sys.path.insert(0, str(Path(SPECPATH).parent))
from backend.blender_bundle import verify_bundle

root = Path(SPECPATH).parent
if not (root / 'frontend' / 'dist' / 'index.html').exists():
    raise SystemExit('Build frontend first: npm run build')

datas = [
    (str(root / 'frontend' / 'dist'), 'frontend/dist'),
    (str(root / 'backend' / 'prompts'), 'backend/prompts'),
    (str(root / 'THIRD_PARTY_NOTICES.md'), '.'),
    (str(root / 'vendor' / 'solidworks-mcp' / 'LICENSE'), 'licenses/solidworks-mcp'),
    (str(root / 'packaging' / 'upstream.json'), 'licenses/solidworks-mcp'),
    (str(root / 'requirements-lock.txt'), 'licenses'),
]
prerequisites = root / 'resources' / 'prerequisites'
bootstrapper = prerequisites / 'MicrosoftEdgeWebview2Setup.exe'
manifest_path = prerequisites / 'webview2-bootstrapper.json'
if not bootstrapper.is_file() or not manifest_path.is_file():
    raise SystemExit('Prepare the verified WebView2 bootstrapper first: scripts/prepare_webview.py')
manifest = json.loads(manifest_path.read_text('utf-8'))
if manifest.get('signature', {}).get('status') != 'Valid' or hashlib.sha256(bootstrapper.read_bytes()).hexdigest() != manifest.get('sha256'):
    raise SystemExit('Bundled WebView2 bootstrapper was changed after verification.')
datas.append((str(prerequisites), 'resources/prerequisites'))
if (root / 'build' / 'dependency-notices' / 'inventory.json').exists():
    datas.append((str(root / 'build' / 'dependency-notices'), 'licenses/dependencies'))
if os.environ.get('FORMA_EXCLUDE_MCP') != '1' and os.environ.get('FORMA_INCLUDE_UPSTREAM_MCP') != '0' and (root / 'resources' / 'mcp' / 'solidworks-mcp.exe').exists():
    folder = root / 'resources' / 'mcp'
    catalog_path = folder / 'tool-catalog.json'
    if not catalog_path.is_file():
        raise SystemExit('Full executor requires a freshly verified tool-catalog.json; run scripts/build-mcp.ps1.')
    catalog = json.loads(catalog_path.read_text('utf-8'))
    if catalog.get('profile') != 'upstream-full' or catalog.get('tool_count') != 161 or catalog.get('excluded_tools') != []:
        raise SystemExit('Refusing to label an incomplete executor as the full tool build.')
    for name in ('solidworks-mcp.dll', 'SolidWorksMcp.Core.dll'):
        if hashlib.sha256((folder / name).read_bytes()).hexdigest() != catalog.get('assembly_sha256', {}).get(name):
            raise SystemExit('Executor changed after catalog verification; verify it again before packaging.')
    datas.append((str(folder), 'resources/mcp'))
if os.environ.get('FORMA_EXCLUDE_MCP') != '1' and (root / 'resources' / 'builtin-mcp' / 'forma-mcp.exe').exists():
    datas.append((str(root / 'resources' / 'builtin-mcp'), 'resources/builtin-mcp'))

blender_folder = root / 'resources' / 'blender'
verify_bundle(blender_folder)
datas.append((str(blender_folder), 'resources/blender'))

binaries, hiddenimports = [], []
for package in ('langgraph', 'langchain_core', 'mcp', 'webview', 'uvicorn'):
    d, b, h = collect_all(package, include_py_files=False,
                          filter_submodules=lambda name: not name.startswith(('mcp.cli', 'mcp.__main__',
                              'webview.platforms.android', 'webview.platforms.cocoa', 'webview.platforms.qt',
                              'webview.platforms.gtk', 'webview.platforms.cef')))
    datas += d
    binaries += b
    hiddenimports += h
hiddenimports += ['webview.platforms.edgechromium', 'uvicorn.logging', 'uvicorn.loops.auto',
                  'uvicorn.protocols.http.auto', 'uvicorn.protocols.websockets.auto', 'uvicorn.lifespan.on']
a = Analysis([str(root / 'desktop.py')], pathex=[str(root)], binaries=binaries, datas=datas,
             hiddenimports=hiddenimports, hookspath=[], hooksconfig={}, runtime_hooks=[],
             excludes=['tkinter', 'IPython', 'notebook'], noarchive=False)
pyz = PYZ(a.pure)
exe = EXE(pyz, a.scripts, [], exclude_binaries=True, name='FormaCAD', debug=False,
          bootloader_ignore_signals=False, strip=False, upx=False, console=False)
coll = COLLECT(exe, a.binaries, a.datas, strip=False, upx=False, name='FormaCAD')
