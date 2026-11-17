"""Prepare the Microsoft-signed WebView2 bootstrapper without executing it.

The bootstrapper is small; the actual Evergreen runtime downloads at installation time.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import subprocess
import sys
from urllib.parse import urlsplit

import httpx

ROOT = Path(__file__).resolve().parents[1]
URL = 'https://go.microsoft.com/fwlink/p/?LinkId=2124703'
FILENAME = 'MicrosoftEdgeWebview2Setup.exe'
MAXIMUM = 4 * 1024 * 1024


def verify_signature(path: Path) -> dict:
    if sys.platform != 'win32':
        raise RuntimeError('Authenticode verification requires the Windows release build machine')
    command = r'''$ErrorActionPreference='Stop'; $s = Get-AuthenticodeSignature -LiteralPath $env:FORMA_VERIFY_BOOTSTRAPPER;
[pscustomobject]@{status=[string]$s.Status;subject=[string]$s.SignerCertificate.Subject;thumbprint=[string]$s.SignerCertificate.Thumbprint} | ConvertTo-Json -Compress'''
    result = subprocess.run(['powershell.exe', '-NoProfile', '-NonInteractive', '-Command', command],
        env={**os.environ, 'FORMA_VERIFY_BOOTSTRAPPER': str(path.resolve())},
        capture_output=True, encoding='utf-8-sig', errors='replace', timeout=60,
        creationflags=subprocess.CREATE_NO_WINDOW)
    if result.returncode:
        raise RuntimeError('Windows Authenticode verification failed; bootstrapper was not accepted')
    signature = json.loads(result.stdout)
    if signature.get('status') != 'Valid' or not re.search(r'(?:^|,\s*)CN=Microsoft Corporation(?:,|$)', signature.get('subject', '')):
        raise RuntimeError(f'Expected a valid Microsoft Authenticode signer, got: {signature.get("status")}')
    return signature


def prepare(destination: Path, refresh: bool = False) -> dict:
    destination.mkdir(parents=True, exist_ok=True)
    target = destination / FILENAME
    temporary = destination / (secrets.token_hex(8) + '.exe')
    try:
        if target.is_file() and not refresh:
            content = target.read_bytes()
        else:
            with httpx.Client(timeout=httpx.Timeout(90, connect=30), follow_redirects=True) as client:
                with client.stream('GET', URL) as response:
                    response.raise_for_status()
                    if urlsplit(str(response.url)).scheme != 'https':
                        raise RuntimeError('Bootstrapper redirect must use HTTPS')
                    blocks, total = [], 0
                    for block in response.iter_bytes():
                        total += len(block)
                        if total > MAXIMUM:
                            raise RuntimeError('WebView2 bootstrapper exceeds the 4 MiB size limit')
                        blocks.append(block)
                    content = b''.join(blocks)
        if not 1024 <= len(content) <= MAXIMUM or not content.startswith(b'MZ'):
            raise RuntimeError('Expected a bounded Windows PE bootstrapper executable')
        temporary.write_bytes(content)
        signature = verify_signature(temporary)
        checksum = hashlib.sha256(content).hexdigest()
        manifest = {'file': FILENAME, 'source_url': URL, 'sha256': checksum, 'bytes': len(content),
                    'signature': signature, 'verified_at': datetime.now(timezone.utc).isoformat(),
                    'requires_internet': True,
                    'note': 'Microsoft WebView2 bootstrapper only. Not executed on the build machine; the runtime itself is downloaded only when missing.'}
        temporary.replace(target)
        (destination / 'webview2-bootstrapper.json').write_text(json.dumps(manifest, indent=2), 'utf-8')
        include = ROOT / 'build' / 'prerequisites' / 'webview2.iss'
        include.parent.mkdir(parents=True, exist_ok=True)
        include.write_text(f'#define WebView2Sha256 "{checksum}"\n', 'ascii')
        print(f'Microsoft WebView2 bootstrapper verified: {len(content)} bytes, SHA256 {checksum}')
        return manifest
    finally:
        temporary.unlink(missing_ok=True)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--refresh', action='store_true')
    args = parser.parse_args()
    prepare(ROOT / 'resources' / 'prerequisites', args.refresh)


if __name__ == '__main__':
    main()
