param([switch]$SkipInstall)
$ErrorActionPreference = 'Stop'
$Root = Split-Path -Parent $PSScriptRoot
$BasePython = Join-Path $Root '.venv\Scripts\python.exe'
$Python = Join-Path $Root 'build\blender-venv\Scripts\python.exe'
if (-not (Test-Path $Python)) {
    & $BasePython -m venv (Join-Path $Root 'build\blender-venv')
    if ($LASTEXITCODE -ne 0) { throw 'Blender build environment creation failed.' }
}
if (-not $SkipInstall) {
    & $BasePython (Join-Path $Root 'scripts\fetch_blender_wheels.py')
    if ($LASTEXITCODE -ne 0) { throw 'Verified Blender build wheel download failed.' }
    & $Python -m pip install --disable-pip-version-check --no-index --find-links (Join-Path $Root 'build\blender-wheels') -r (Join-Path $Root 'build\blender-wheels\resolved.txt')
    if ($LASTEXITCODE -ne 0) { throw 'Pinned Blender build dependency installation failed.' }
}
& $Python (Join-Path $Root 'scripts\prepare_blender_bundle.py')
if ($LASTEXITCODE -ne 0) { throw 'Blender addon/resource preparation failed.' }
& $Python -m PyInstaller --noconfirm --clean --distpath (Join-Path $Root 'build\blender-dist') --workpath (Join-Path $Root 'build\blender-pyinstaller') (Join-Path $Root 'packaging\blender.spec')
if ($LASTEXITCODE -ne 0) { throw 'Standalone Blender MCP build failed.' }
& $Python (Join-Path $Root 'scripts\prepare_blender_bundle.py') --finalize
if ($LASTEXITCODE -ne 0) { throw 'Blender bundle manifest generation failed.' }
& $BasePython (Join-Path $Root 'scripts\verify_blender_bundle.py')
if ($LASTEXITCODE -ne 0) { throw 'Standalone Blender MCP protocol verification failed.' }
Write-Host 'Bundled Blender MCP: resources\blender\server\forma-blender-mcp.exe'
