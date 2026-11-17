"""Provision a project-local .NET SDK from Microsoft's hash-verified SDK ZIP."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import zipfile

import httpx

ROOT = Path(__file__).resolve().parents[1]
VERSION = '10.0.403'
URL = f'https://builds.dotnet.microsoft.com/dotnet/Sdk/{VERSION}/dotnet-sdk-{VERSION}-win-x64.zip'
SHA512 = '7a114c318fa7f1d9822991b534b2814d0e78386ceda816b371f5141acefe741d87ccb606ede06cadac8f9ac5070e32b708a67bbd1619ff5e5a7858a27874ef48'


def main():
    destination = ROOT / 'build' / 'dotnet'
    archive = ROOT / 'build' / f'dotnet-sdk-{VERSION}-win-x64.zip'
    destination.mkdir(parents=True, exist_ok=True)
    if not archive.is_file():
        print(f'Downloading Microsoft .NET SDK {VERSION}', flush=True)
        temp = archive.with_suffix('.download')
        with httpx.stream('GET', URL, follow_redirects=True, timeout=httpx.Timeout(300, connect=30)) as response:
            response.raise_for_status()
            with temp.open('wb') as output:
                for block in response.iter_bytes(1024 * 1024):
                    output.write(block)
        temp.replace(archive)
    digest = hashlib.sha512()
    with archive.open('rb') as source:
        for block in iter(lambda: source.read(1024 * 1024), b''):
            digest.update(block)
    if digest.hexdigest() != SHA512:
        raise SystemExit('SDK SHA-512 mismatch; refusing to extract.')
    if not (destination / 'sdk' / VERSION / 'dotnet.dll').is_file():
        with zipfile.ZipFile(archive) as bundle:
            for entry in bundle.infolist():
                target = (destination / entry.filename).resolve()
                if not target.is_relative_to(destination.resolve()):
                    raise SystemExit('SDK archive contains an invalid member path')
            bundle.extractall(destination)
    (destination / 'forma-sdk-source.json').write_text(json.dumps({'version': VERSION, 'url': URL, 'sha512': SHA512}, indent=2), 'utf-8')
    print(f'Project-local SDK ready: {destination / "dotnet.exe"}', flush=True)


if __name__ == '__main__':
    main()
