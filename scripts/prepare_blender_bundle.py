"""Prepare version-matched, offline Blender addon dependencies and build provenance."""
from __future__ import annotations
import argparse
import hashlib
import importlib.metadata as metadata
import importlib.util
import json
from pathlib import Path
import shutil
import zipfile

ROOT = Path(__file__).resolve().parents[1]
DEST = ROOT / 'resources' / 'blender'
VERSION = '2.2.7'


def prepare():
    if metadata.version('mcp-for-blender') != VERSION:
        raise RuntimeError('Build environment has an unexpected Blender MCP version')
    package = Path(importlib.util.find_spec('blender_mcp').origin).parent
    addon = package / 'bundled' / 'addon.py'
    if not addon.is_file():
        raise RuntimeError('Pinned package is missing its bundled addon')
    DEST.mkdir(parents=True, exist_ok=True)
    shutil.copy2(addon, DEST / 'addon.py')
    shutil.copy2(ROOT / 'packaging' / 'blender_bootstrap.py', DEST / 'forma_start.py')
    # Blender embeds a different Python version; ship only portable Python and data, no build-ABI extensions.
    vendor = DEST / 'addon-deps'
    if vendor.exists():
        shutil.rmtree(vendor)
    vendor.mkdir()
    for name in ('requests', 'urllib3', 'certifi', 'idna', 'charset_normalizer'):
        source = Path(importlib.util.find_spec(name).origin).parent
        shutil.copytree(source, vendor / name, ignore=shutil.ignore_patterns('__pycache__', '*.pyc', '*.pyd', '*.so'))
    notices = DEST / 'licenses'
    notices.mkdir(exist_ok=True)
    inventory = []
    for distribution in metadata.distributions():
        name = distribution.metadata.get('Name', 'unknown')
        files = []
        for entry in distribution.files or []:
            if 'license' not in entry.name.lower() and entry.name.lower() not in {'copying', 'notice', 'authors'}:
                continue
            source = Path(distribution.locate_file(entry))
            if source.is_file():
                target = notices / name / source.name
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(source, target)
                files.append(target.relative_to(DEST).as_posix())
        inventory.append({'name': name, 'version': distribution.version, 'notice_files': files})
    if not any(item['name'] == 'mcp-for-blender' and item['notice_files'] for item in inventory):
        raise RuntimeError('Pinned Blender package license is missing')
    (DEST / 'dependency-inventory.json').write_text(json.dumps(inventory, indent=2), 'utf-8')
    (DEST / 'build-requirements.txt').write_text((ROOT / 'packaging' / 'blender-requirements.txt').read_text('utf-8'), 'utf-8')
    # Manual offline fallback: install a single ZIP in Blender, with dependencies inside the module folder.
    with zipfile.ZipFile(DEST / 'forma-blender-addon.zip', 'w', zipfile.ZIP_DEFLATED) as archive:
        archive.writestr('forma_blender_addon/__init__.py',
            'import sys\nfrom pathlib import Path\nsys.path.insert(0, str(Path(__file__).parent / "_vendor"))\n' + addon.read_text('utf-8'))
        for path in sorted(vendor.rglob('*')):
            if path.is_file():
                archive.write(path, 'forma_blender_addon/_vendor/' + path.relative_to(vendor).as_posix())
    print('Prepared matching addon, bootstrap, portable dependencies and license notices.')


def finalize():
    server = ROOT / 'build' / 'blender-dist' / 'forma-blender-mcp'
    if not (server / 'forma-blender-mcp.exe').is_file():
        raise RuntimeError('Build the standalone server before finalizing')
    destination = DEST / 'server'
    if destination.exists():
        shutil.rmtree(destination)
    shutil.copytree(server, destination)
    files = {path.relative_to(DEST).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
             for path in sorted(DEST.rglob('*')) if path.is_file() and path.name != 'bundle.json'}
    manifest = {'format': 1, 'package': 'mcp-for-blender', 'version': VERSION,
                'source': 'https://pypi.org/project/mcp-for-blender/2.2.7/',
                'executable': 'server/forma-blender-mcp.exe', 'addon': 'addon.py',
                'bootstrap': 'forma_start.py', 'files': files}
    (DEST / 'bundle.json').write_text(json.dumps(manifest, indent=2), 'utf-8')
    print(f'Bundled standalone Blender MCP with {len(files)} hashed files.')


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--finalize', action='store_true')
    args = parser.parse_args()
    finalize() if args.finalize else prepare()
