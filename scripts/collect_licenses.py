"""Generate release dependency notices from the locked Python and npm environment."""
from __future__ import annotations

import importlib.metadata as metadata
import json
import shutil
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DESTINATION = ROOT / 'build' / 'dependency-notices'


def main():
    DESTINATION.mkdir(parents=True, exist_ok=True)
    inventory = []
    for distribution in metadata.distributions():
        name = distribution.metadata.get('Name', 'unknown')
        files = []
        for entry in distribution.files or []:
            if 'license' not in entry.name.lower() and entry.name.lower() not in ('copying', 'notice', 'authors'):
                continue
            source = Path(distribution.locate_file(entry))
            if source.is_file():
                target = DESTINATION / 'python' / name / Path(str(entry)).name
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(source, target)
                files.append(str(target.relative_to(DESTINATION)))
        inventory.append({'ecosystem': 'python', 'name': name, 'version': distribution.version,
                          'license': distribution.metadata.get('License-Expression') or distribution.metadata.get('License', ''),
                          'notice_files': files})
    modules = ROOT / 'frontend' / 'node_modules'
    for package_path in modules.rglob('package.json'):
        if 'node_modules' in package_path.parts and package_path.parent.name == '.vite':
            continue
        try:
            package = json.loads(package_path.read_text('utf-8'))
        except (ValueError, OSError):
            continue
        name = package.get('name')
        if not name or not package.get('version'):
            continue
        files = []
        for source in package_path.parent.iterdir():
            if source.is_file() and source.name.lower().startswith(('license', 'copying', 'notice')):
                target = DESTINATION / 'npm' / name.replace('/', '__') / source.name
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(source, target)
                files.append(str(target.relative_to(DESTINATION)))
        inventory.append({'ecosystem': 'npm', 'name': name, 'version': package['version'],
                          'license': package.get('license', ''), 'notice_files': files})
    (DESTINATION / 'inventory.json').write_text(json.dumps(inventory, ensure_ascii=False, indent=2), 'utf-8')
    print(f'Collected dependency metadata for {len(inventory)} packages into {DESTINATION}')


if __name__ == '__main__':
    main()
