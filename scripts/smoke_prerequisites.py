"""Check the real frozen EXE's read-only dependency report without opening any UI.

This does not install or uninstall WebView2 on the build machine.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import secrets
import subprocess
import tempfile

import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from backend import __version__


def main():
    with tempfile.TemporaryDirectory(prefix='prerequisite-smoke-', dir=ROOT / 'build') as folder:
        directory = Path(folder)
        profile = directory / 'profile'
        report_path = directory / 'runtime-report.json'
        secret = secrets.token_urlsafe(24)
        env = dict(os.environ, FORMA_DATA_DIR=str(profile), FORMA_API_KEY=secret,
                   TEMP=str(ROOT / 'build' / 'temp'), TMP=str(ROOT / 'build' / 'temp'))
        result = subprocess.run([str(ROOT / 'dist' / 'FormaCAD' / 'FormaCAD.exe'), '--check-runtime', str(report_path)],
                                env=env, timeout=30)
        assert result.returncode == 0
        raw = report_path.read_text('utf-8')
        report = json.loads(raw)
        assert secret not in raw
        assert report['app_version'] == __version__
        assert report['webview_bootstrapper']['available']
        assert report['webview_bootstrapper']['requires_internet']
        assert report['webview_bootstrapper']['publisher'] == 'Microsoft Corporation'
        assert not (profile / 'forma.db').exists(), 'Read-only startup check must not create the app database'
        assert not (profile / 'executor').exists(), 'Read-only startup check must not launch the executor'
        print('Frozen read-only runtime diagnostics and bundled WebView2 payload integrity passed; no installer, CAD or model was started.')


if __name__ == '__main__':
    main()
