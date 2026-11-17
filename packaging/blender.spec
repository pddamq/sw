# -*- mode: python ; coding: utf-8 -*-
from pathlib import Path
from PyInstaller.utils.hooks import collect_all, copy_metadata
root = Path(SPECPATH).parent
if not (root / 'packaging' / 'blender-requirements.txt').is_file():
    raise SystemExit('Unexpected build root')
datas, binaries, hiddenimports = [], [], []
for name in ('blender_mcp', 'mcp'):
    d, b, h = collect_all(name, include_py_files=False,
                          filter_submodules=lambda module: not module.startswith('mcp.cli'))
    datas += d
    binaries += b
    hiddenimports += h
for name in ('mcp-for-blender', 'mcp'):
    datas += copy_metadata(name)
a = Analysis([str(root / 'packaging' / 'blender_entry.py')], pathex=[str(root)],
             binaries=binaries, datas=datas, hiddenimports=hiddenimports,
             hookspath=[], hooksconfig={}, runtime_hooks=[], excludes=['tkinter', 'IPython', 'pytest'], noarchive=False)
pyz = PYZ(a.pure)
exe = EXE(pyz, a.scripts, [], exclude_binaries=True, name='forma-blender-mcp', console=True,
          debug=False, bootloader_ignore_signals=False, strip=False, upx=False)
coll = COLLECT(exe, a.binaries, a.datas, name='forma-blender-mcp', strip=False, upx=False)
