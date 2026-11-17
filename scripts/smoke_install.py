"""Install a preview into a disposable build folder, verify its UI, then uninstall it."""
from __future__ import annotations

import os
from pathlib import Path
import subprocess

ROOT = Path(__file__).resolve().parents[1]


def main():
    installer = ROOT / 'dist' / 'installer' / 'FormaCAD-Full-Preview-Setup-0.8.1-x64.exe'
    destination = ROOT / 'build' / 'installed-smoke'
    temp_dir = ROOT / 'build' / 'setup-temp'
    temp_dir.mkdir(parents=True, exist_ok=True)
    env = dict(os.environ, FORMA_DATA_DIR=str(ROOT / 'build' / 'installed-smoke-runtime'),
               TEMP=str(temp_dir), TMP=str(temp_dir))
    result = subprocess.run([str(installer), '/VERYSILENT', '/SUPPRESSMSGBOXES', '/NORESTART', '/NOICONS',
                             '/TASKS=', f'/DIR={destination}', f'/LOG={ROOT / "build" / "install-smoke.log"}'],
                            timeout=90, env=env)
    if result.returncode:
        raise RuntimeError(f'Installer exited with {result.returncode}')
    try:
        result = subprocess.run([str(destination / 'FormaCAD.exe'), '--smoke-test'], timeout=45, env=env)
        if result.returncode:
            raise RuntimeError(f'Installed UI smoke test exited with {result.returncode}')
        print('Installed desktop WebView2 UI loaded successfully.')
    finally:
        uninstaller = destination / 'unins000.exe'
        if uninstaller.exists():
            result = subprocess.run([str(uninstaller), '/VERYSILENT', '/SUPPRESSMSGBOXES', '/NORESTART'], timeout=60)
            if result.returncode:
                raise RuntimeError(f'Uninstaller exited with {result.returncode}')
    print('Preview installer / launch / uninstall smoke test passed on this machine.')


if __name__ == '__main__':
    main()
