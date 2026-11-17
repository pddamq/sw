"""Fetch checksum-pinned compile-only COM metadata; never replace upstream tool bodies.

Interop reference DLLs remain in build/. The upstream project embeds the COM type metadata;
SOLIDWORKS and its licensed modules must still be installed on the target computer.
"""
from __future__ import annotations

import hashlib
import io
import json
from pathlib import Path
from xml.sax.saxutils import escape
import zipfile

import httpx

ROOT = Path(__file__).resolve().parents[1]
BUILD = ROOT / 'build' / 'native-full'
PACKAGES = [
    ('visiativ.solidworks.interop', '34.0.0', 'ae77be5a9be17948ce5d9e1f82708b9d840d39d21f07d987e4c84a5c22193454', {
        'lib/net48/SolidWorks.Interop.sldworks.dll': 'SolidWorks.Interop.sldworks.dll',
        'lib/net48/SolidWorks.Interop.swconst.dll': 'SolidWorks.Interop.swconst.dll',
    }),
    ('bluebyte.solidworks.costing.interop', '2025.5.0', 'a76eedbc2f0a4c44cd97866432e7230550a734bb79ea1ec89fa04caf78b13a0a', {
        'lib/SolidWorks.Interop.sldcostingapi.dll': 'SolidWorks.Interop.sldcostingapi.dll',
    }),
    ('bluebyte.solidworks.featureworks.interop', '2023.1.0', '6e3f1c98cf244db4b57aac7c3a3647de425bd2a5bf919d61f8dfb5b0d5384c74', {
        'lib/SolidWorks.Interop.fworks.dll': 'SolidWorks.Interop.fworks.dll',
    }),
    ('hawkware.solidworks', '28.1.0.74', '9083168dbd40bd1500027f44f925c744c8d4fffd7d6c0d3aff241042bc99dbee', {
        'lib/SolidWorks.Interop.cosworks.dll': 'SolidWorks.Interop.cosworks.dll',
    }),
]


def main():
    refs = BUILD / 'references'
    refs.mkdir(parents=True, exist_ok=True)
    metadata = BUILD / 'reference-metadata'
    metadata.mkdir(exist_ok=True)
    provenance = []
    with httpx.Client(transport=httpx.HTTPTransport(retries=3), timeout=60, follow_redirects=True) as client:
        for package, version, checksum, members in PACKAGES:
            cache = BUILD / f'{package}.{version}.nupkg'
            url = f'https://api.nuget.org/v3-flatcontainer/{package}/{version}/{package}.{version}.nupkg'
            if not cache.exists():
                response = client.get(url)
                response.raise_for_status()
                raw = response.content
            else:
                raw = cache.read_bytes()
            if hashlib.sha256(raw).hexdigest() != checksum:
                raise ValueError(f'Compile reference checksum mismatch: {package} {version}')
            if not cache.exists():
                cache.write_bytes(raw)
            with zipfile.ZipFile(io.BytesIO(raw)) as archive:
                for member, name in members.items():
                    (refs / name).write_bytes(archive.read(member))
                for member in archive.namelist():
                    if member.endswith('.nuspec') or Path(member).name.lower().startswith(('license', 'notice', 'copying')):
                        (metadata / (package + '-' + Path(member).name)).write_bytes(archive.read(member))
            provenance.append({'package': package, 'version': version, 'url': url, 'sha256': checksum,
                               'members': members, 'usage': 'compile-only embedded COM type metadata; original reference DLLs are not shipped'})
    (BUILD / 'compile-references.json').write_text(json.dumps({'references': provenance,
        'cad_live_validated': False, 'note': 'Versions differ between reference assemblies. Compilation/catalog checks do not establish runtime SW2023 or module compatibility.'}, indent=2), 'utf-8')
    (BUILD / 'FullExecutor.targets').write_text(
        '<Project><ItemGroup Condition="\'$(MSBuildProjectName)\' == \'SolidWorksMcp.Core\'">'
        f'<Compile Include="{escape(str(ROOT / "executor" / "SimulationVersionDispatch.cs"))}" />'
        '</ItemGroup></Project>', 'utf-8')
    print(f'Prepared all five compile references at {refs}')


if __name__ == '__main__':
    main()
