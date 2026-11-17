"""Extract the portable archive and launch its real hidden desktop UI."""
from __future__ import annotations

import os
from pathlib import Path
import subprocess
import tempfile
import zipfile

import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from backend import __version__


def main():
    archives = list((ROOT / 'dist').glob(f'FormaCAD*-{__version__}-x64.zip'))
    if not archives:
        raise RuntimeError(f'Build a {__version__} portable archive first.')
    archive = max(archives, key=lambda item: item.stat().st_mtime)
    with tempfile.TemporaryDirectory(prefix='portable-smoke-', dir=ROOT / 'build') as directory:
        with zipfile.ZipFile(archive) as bundle:
            bundle.extractall(directory)
        env = dict(os.environ, FORMA_DATA_DIR=str(ROOT / 'build' / 'portable-smoke-runtime'))
        result = subprocess.run([str(Path(directory) / 'FormaCAD' / 'FormaCAD.exe'), '--smoke-test'],
                                timeout=45, env=env)
        if result.returncode:
            raise RuntimeError(f'Extracted desktop failed with exit code {result.returncode}')
    print('Portable ZIP extraction and real WebView2 UI startup passed.')


if __name__ == '__main__':
    main()
