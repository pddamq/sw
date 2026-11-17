"""Build preparation for a self-contained core profile of the upstream C# executor.

The upstream checkout stays unchanged. Generated projects omit only tools whose
optional Simulation/FeatureWorks compile references are absent. No stub CAD calls
or fake tool success are introduced.
"""
from __future__ import annotations

import hashlib
import io
import json
from pathlib import Path
import re
from xml.sax.saxutils import escape
import zipfile

import httpx

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / 'vendor' / 'solidworks-mcp' / 'src'
BUILD = ROOT / 'build' / 'native-core'
REFS = BUILD / 'references'
PACKAGES = [
    ('visiativ.solidworks.interop', '34.0.0', {
        'lib/net48/SolidWorks.Interop.sldworks.dll': 'SolidWorks.Interop.sldworks.dll',
        'lib/net48/SolidWorks.Interop.swconst.dll': 'SolidWorks.Interop.swconst.dll',
    }),
    ('bluebyte.solidworks.costing.interop', '2025.5.0', {
        'lib/SolidWorks.Interop.sldcostingapi.dll': 'SolidWorks.Interop.sldcostingapi.dll',
    }),
]
OMITTED = ['sim_static', 'sim_run', 'sim_plot', 'sim_list', 'sim_delete', 'feature_recognize']


def remove_tool(source: str, name: str) -> str:
    pattern = re.compile(r'    \[McpServerTool\(Name = "' + re.escape(name) + r'".*?(?=    \[McpServerTool\(|\Z)', re.S)
    source, count = pattern.subn('', source)
    if count != 1:
        raise RuntimeError(f'Expected exactly one upstream tool block: {name}')
    return source


def main():
    REFS.mkdir(parents=True, exist_ok=True)
    provenance = []
    with httpx.Client(transport=httpx.HTTPTransport(retries=3), timeout=60, follow_redirects=True) as client:
        for package, version, members in PACKAGES:
            url = f'https://api.nuget.org/v3-flatcontainer/{package}/{version}/{package}.{version}.nupkg'
            cache = BUILD / f'{package}.{version}.nupkg'
            if not cache.exists():
                response = client.get(url)
                response.raise_for_status()
                cache.write_bytes(response.content)
            raw = cache.read_bytes()
            with zipfile.ZipFile(io.BytesIO(raw)) as bundle:
                if '.signature.p7s' not in bundle.namelist():
                    raise RuntimeError('Reference package is missing its NuGet signature')
                for member, name in members.items():
                    (REFS / name).write_bytes(bundle.read(member))
            provenance.append({'package': package, 'version': version, 'url': url,
                               'sha256': hashlib.sha256(raw).hexdigest(),
                               'usage': 'compile-only embedded interop types; reference DLLs are not copied to runtime output'})
    core = BUILD / 'Core'
    server = BUILD / 'Server'
    core.mkdir(exist_ok=True); server.mkdir(exist_ok=True)
    props = '''<Project><PropertyGroup>
<TargetFramework>net10.0-windows</TargetFramework><LangVersion>latest</LangVersion>
<Nullable>enable</Nullable><ImplicitUsings>enable</ImplicitUsings><Version>0.1.0-forma-core.1</Version>
<EnableDefaultCompileItems>false</EnableDefaultCompileItems><IsTrimmable>false</IsTrimmable>
<PublishTrimmed>false</PublishTrimmed><PackageLicenseExpression>MIT</PackageLicenseExpression>
</PropertyGroup></Project>'''
    (BUILD / 'Directory.Build.props').write_text(props, 'utf-8')
    def path(value):
        return escape(str(value))
    refs = ''.join(f'<Reference Include="{name[:-4]}"><HintPath>{path(REFS / name)}</HintPath>'
                   '<EmbedInteropTypes>true</EmbedInteropTypes><Private>false</Private></Reference>'
                   for _, _, members in PACKAGES for name in members.values())
    excluded = [SOURCE / 'SolidWorksMcp.Core' / 'Services' / 'SimulationService*.cs',
                SOURCE / 'SolidWorksMcp.Core' / 'Services' / 'RecognitionService.cs',
                SOURCE / 'SolidWorksMcp.Core' / 'Com' / 'SwExt.Simulation.cs',
                SOURCE / 'SolidWorksMcp.Core' / 'obj' / '**' / '*.cs']
    (core / 'SolidWorksMcp.Core.csproj').write_text(f'''<Project Sdk="Microsoft.NET.Sdk">
<PropertyGroup><AssemblyName>SolidWorksMcp.Core</AssemblyName><RootNamespace>SolidWorksMcp.Core</RootNamespace></PropertyGroup>
<ItemGroup><Compile Include="{path(SOURCE / 'SolidWorksMcp.Core' / '**' / '*.cs')}" Exclude="{';'.join(path(p) for p in excluded)}" /></ItemGroup>
<ItemGroup>{refs}<PackageReference Include="Microsoft.Extensions.Logging.Abstractions" Version="10.0.13" /></ItemGroup>
<Target Name="ForbidDynamic" AfterTargets="ResolveTargetingPackAssets"><ItemGroup><Reference Remove="@(Reference)" Condition="'%(Filename)' == 'Microsoft.CSharp'" /></ItemGroup></Target>
</Project>''', 'utf-8')
    feature_file = SOURCE / 'SolidWorksMcp.Server' / 'Tools' / 'FeatureManagementTools.cs'
    (server / feature_file.name).write_text(remove_tool(feature_file.read_text('utf-8'), 'feature_recognize'), 'utf-8')
    setup_file = SOURCE / 'SolidWorksMcp.Server' / 'Hosting' / 'ServerSetup.cs'
    setup = setup_file.read_text('utf-8')
    setup = setup.replace('o.ServerInstructions = Instructions.Text;',
        'o.ServerInstructions = Instructions.Text + "\\nFORMA CORE PROFILE: Simulation and FeatureWorks recognition tools are not included in this build. Use the actually listed tools; no other capabilities are implied.";')
    (server / setup_file.name).write_text(setup, 'utf-8')
    excluded_server = [SOURCE / 'SolidWorksMcp.Server' / 'Tools' / 'SimulationTools.cs', feature_file, setup_file,
                       SOURCE / 'SolidWorksMcp.Server' / 'obj' / '**' / '*.cs']
    (server / 'SolidWorksMcp.Server.csproj').write_text(f'''<Project Sdk="Microsoft.NET.Sdk">
<PropertyGroup><OutputType>Exe</OutputType><AssemblyName>solidworks-mcp</AssemblyName><RootNamespace>SolidWorksMcp.Server</RootNamespace></PropertyGroup>
<ItemGroup><Compile Include="{path(SOURCE / 'SolidWorksMcp.Server' / '**' / '*.cs')}" Exclude="{';'.join(path(p) for p in excluded_server)}" />
<Compile Include="*.cs" /></ItemGroup>
<ItemGroup><PackageReference Include="ModelContextProtocol" Version="2.3.0" /><PackageReference Include="Microsoft.Extensions.Hosting" Version="10.0.13" />
<ProjectReference Include="..\\Core\\SolidWorksMcp.Core.csproj" /></ItemGroup></Project>''', 'utf-8')
    manifest = {'profile': 'forma-core', 'upstream_repository': 'https://github.com/soheil-mp/solidworks-mcp',
                'upstream_commit': '79fb529b67ca4a0587e5570024a772fad0b2c14d',
                'excluded_tools': OMITTED, 'compile_references': provenance,
                'live_cad_verified': False, 'sw2023_verified': False,
                'note': 'Core profile omits optional Simulation and FeatureWorks. All remaining tool bodies are upstream implementations.'}
    (BUILD / 'executor-profile.json').write_text(json.dumps(manifest, indent=2), 'utf-8')
    print(f'Prepared core executor project: {server / "SolidWorksMcp.Server.csproj"}', flush=True)


if __name__ == '__main__':
    main()
