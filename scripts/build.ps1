param(
    [switch]$SkipMcp,
    [switch]$WithUpstreamMcp,
    [switch]$BasicMcp,
    [switch]$Installer,
    [switch]$SkipBlenderBuild,
    [string]$SolidWorksDir = $env:SOLIDWORKS_DIR
)
$ErrorActionPreference = 'Stop'
$Root = Split-Path -Parent $PSScriptRoot
Set-Location $Root
$TempDir = Join-Path $Root 'build\temp'
New-Item -ItemType Directory -Force -Path $TempDir | Out-Null
$env:TEMP = $TempDir
$env:TMP = $TempDir
$Python = Join-Path $Root '.venv\Scripts\python.exe'
if (-not (Test-Path $Python)) {
    & python -m venv .venv
    if ($LASTEXITCODE -ne 0) { throw 'Python venv creation failed' }
}
& $Python -m pip install --disable-pip-version-check --no-cache-dir -r requirements-lock.txt
if ($LASTEXITCODE -ne 0) { throw 'Dependency installation failed' }
Push-Location (Join-Path $Root 'frontend')
try {
    & npm ci
    if ($LASTEXITCODE -ne 0) { throw 'Frontend dependency installation failed' }
    & npm test
    if ($LASTEXITCODE -ne 0) { throw 'Frontend tests failed' }
    & npm run build
    if ($LASTEXITCODE -ne 0) { throw 'Frontend build failed' }
} finally { Pop-Location }
if (($SkipMcp -and ($WithUpstreamMcp -or $BasicMcp)) -or ($WithUpstreamMcp -and $BasicMcp)) {
    throw 'Choose the default full build, -BasicMcp, or -SkipMcp.'
}
$FullMcp = -not ($SkipMcp -or $BasicMcp)
$env:FORMA_REQUIRE_FULL_MCP = if ($FullMcp) { '1' } else { '0' }
$env:FORMA_EXCLUDE_MCP = if ($SkipMcp) { '1' } else { '0' }
$env:FORMA_INCLUDE_UPSTREAM_MCP = if ($FullMcp) { '1' } else { '0' }
if (-not $SkipMcp) {
    & (Join-Path $PSScriptRoot 'build-builtin-mcp.ps1')
    if ($FullMcp) {
        & (Join-Path $PSScriptRoot 'build-mcp.ps1') -SolidWorksDir $SolidWorksDir
    }
} else {
    Write-Warning 'Building an APPLICATION-ONLY preview without either C# executor.'
}
if (-not $SkipBlenderBuild) {
    & (Join-Path $PSScriptRoot 'build-blender.ps1')
}
& $Python scripts\verify_blender_bundle.py
if ($LASTEXITCODE -ne 0) { throw 'Verified bundled Blender MCP is required for release.' }
& $Python scripts\prepare_webview.py
if ($LASTEXITCODE -ne 0) { throw 'Microsoft WebView2 bootstrapper download/signature verification failed; release stopped.' }
& $Python -m pytest -q
if ($LASTEXITCODE -ne 0) { throw 'Tests failed; packaging stopped' }
& $Python scripts\collect_licenses.py
if ($LASTEXITCODE -ne 0) { throw 'Dependency notice generation failed' }
& $Python -m PyInstaller --noconfirm --clean packaging\forma.spec
if ($LASTEXITCODE -ne 0) { throw 'Desktop executable build failed' }
& $Python scripts\finalize_release.py
if ($LASTEXITCODE -ne 0) { throw 'Release finalization failed' }
& $Python scripts\make_portable.py
if ($LASTEXITCODE -ne 0) { throw 'Portable archive creation failed' }
if ($Installer) {
    $Compiler = Get-Command ISCC.exe -ErrorAction SilentlyContinue
    if ($Compiler) { $Iscc = $Compiler.Source }
    elseif (Test-Path "$env:LOCALAPPDATA\Programs\Inno Setup 6\ISCC.exe") {
        $Iscc = "$env:LOCALAPPDATA\Programs\Inno Setup 6\ISCC.exe"
    } else { $Iscc = "${env:ProgramFiles(x86)}\Inno Setup 6\ISCC.exe" }
    if (-not (Test-Path $Iscc)) { throw 'Install Inno Setup 6 to create Setup.exe' }
    if ($SkipMcp) { & $Iscc /DPreviewBuild packaging\installer.iss }
    elseif ($FullMcp) { & $Iscc /DFullBuild packaging\installer.iss }
    else { & $Iscc /DBasicBuild packaging\installer.iss }
    if ($LASTEXITCODE -ne 0) { throw 'Installer build failed' }
}
Write-Host 'Desktop application: dist\FormaCAD\FormaCAD.exe'
if ($SkipMcp) { Write-Warning 'This build needs an external C# MCP executable configured in Settings.' }
