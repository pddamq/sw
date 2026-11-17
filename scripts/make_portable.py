from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import shutil
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ.setdefault('FORMA_DATA_DIR', str(ROOT / 'build' / 'release-runtime'))
from backend import __version__
from backend.prerequisites import bootstrapper_info
from backend.blender_bundle import verify_bundle


def main():
    app = ROOT / 'dist' / 'FormaCAD'
    if not (app / 'FormaCAD.exe').exists():
        raise SystemExit('Build the desktop application first.')
    folder = app / '_internal' / 'resources' / 'mcp'
    upstream = (folder / 'solidworks-mcp.exe').is_file()
    basic = (app / '_internal' / 'resources' / 'builtin-mcp' / 'forma-mcp.exe').is_file()
    catalog = {}
    if upstream:
        catalog = json.loads((folder / 'tool-catalog.json').read_text('utf-8'))
        if catalog.get('tool_count') != 161 or catalog.get('excluded_tools') != [] or not catalog.get('catalog_complete'):
            raise SystemExit('Full release requires all 161 tools to be verified.')
        for name in ('solidworks-mcp.dll', 'SolidWorksMcp.Core.dll'):
            if hashlib.sha256((folder / name).read_bytes()).hexdigest() != catalog.get('assembly_sha256', {}).get(name):
                raise SystemExit('Full executor changed after verification.')
    blender = verify_bundle(app / '_internal' / 'resources' / 'blender')
    bootstrapper = bootstrapper_info(app / '_internal')
    if not bootstrapper['available']:
        raise SystemExit('Distribution is missing its verified WebView2 repair bootstrapper.')
    prefix = 'FormaCAD-Full-Preview' if upstream else ('FormaCAD-Basic-Preview' if basic else 'FormaCAD-Preview')
    name = f'{prefix}-{__version__}-x64'
    archive = Path(shutil.make_archive(str(ROOT / 'dist' / name), 'zip', ROOT / 'dist', 'FormaCAD'))
    digest = hashlib.sha256()
    with archive.open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(block)
    manifest = {'file': archive.name, 'bytes': archive.stat().st_size, 'sha256': digest.hexdigest(),
                'bundled_csharp_mcp': upstream or basic,
                'executor_flavor': 'upstream-full' if upstream else ('builtin-basic' if basic else 'external-required'),
                'primary_tool_count': catalog.get('tool_count', 18 if basic else 0),
                'basic_fallback_tool_count': 18 if basic else 0, 'excluded_tools': catalog.get('excluded_tools', []),
                'upstream_csharp_mcp': upstream, 'vba_default_enabled': False, 'cad_live_validation': False,
                'blender_mcp_included': True, 'blender_mcp_version': blender['version'],
                'blender_addon_included': True, 'blender_application_included': False,
                'blender_live_validation': False,
                'webview2_bootstrapper_included': True, 'webview2_bootstrapper_sha256': bootstrapper['sha256'],
                'webview2_repair_requires_internet': True,
                'note': 'Extract the entire archive. Catalog completeness does not imply verified CAD results. Simulation/FeatureWorks require installed modules and licenses.'}
    (ROOT / 'dist' / f'{name}.json').write_text(json.dumps(manifest, indent=2), 'utf-8')
    print(f'Portable archive: {archive} ({archive.stat().st_size / 1024 / 1024:.1f} MiB)')


if __name__ == '__main__':
    main()
