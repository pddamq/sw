"""Application-owned validation of the offline Blender runtime resource bundle."""
from __future__ import annotations
import hashlib
import json
from pathlib import Path

VERSION = '2.2.7'
REQUIRED = ('server/forma-blender-mcp.exe', 'addon.py', 'forma_start.py', 'dependency-inventory.json', 'forma-blender-addon.zip')


def verify_bundle(folder: Path, *, full: bool = True) -> dict:
    try:
        manifest_path = folder / 'bundle.json'
        if manifest_path.stat().st_size > 512 * 1024:
            raise ValueError('Manifest too large')
        manifest = json.loads(manifest_path.read_text('utf-8'))
        if manifest.get('format') != 1 or manifest.get('package') != 'mcp-for-blender' or manifest.get('version') != VERSION:
            raise ValueError('Unexpected Blender bundle version')
        files = manifest.get('files')
        if not isinstance(files, dict) or not all(name in files for name in REQUIRED):
            raise ValueError('Required Blender resources missing')
        for name, digest in files.items():
            path = Path(name)
            if (not isinstance(name, str) or '\\' in name or ':' in name or path.is_absolute()
                    or '..' in path.parts or not isinstance(digest, str) or len(digest) != 64):
                raise ValueError('Invalid Blender resource manifest')
            target = (folder / path).resolve()
            if not target.is_relative_to(folder.resolve()) or not target.is_file():
                raise ValueError('Blender resource missing')
            if (full or name in REQUIRED) and hashlib.sha256(target.read_bytes()).hexdigest() != digest:
                raise ValueError('Blender resource changed after build')
        return manifest
    except (OSError, ValueError, TypeError, AttributeError) as exc:
        raise ValueError('内置 Blender MCP 资源缺失或校验失败，请重新安装完整应用。') from exc
