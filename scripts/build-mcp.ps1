param(
    [string]$SolidWorksDir = $env:SOLIDWORKS_DIR,
    [string]$InteropDir = '',
    [string]$Dotnet = '',
    [switch]$SkipPrepare
)
$ErrorActionPreference = 'Stop'
$Root = Split-Path -Parent $PSScriptRoot
$Source = Join-Path $Root 'vendor\solidworks-mcp\src\SolidWorksMcp.Server\SolidWorksMcp.Server.csproj'
$Destination = Join-Path $Root 'resources\mcp'
$Python = Join-Path $Root '.venv\Scripts\python.exe'
if (-not $Dotnet) {
    $Installed = Get-Command dotnet -ErrorAction SilentlyContinue
    $Dotnet = if ($Installed) { $Installed.Source } else { Join-Path $Root 'build\dotnet\dotnet.exe' }
}
if (-not (Test-Path $Dotnet)) { throw 'Install the .NET 10 SDK or supply -Dotnet with its full executable path.' }
if (-not (Test-Path $Source)) { throw 'The pinned upstream solidworks-mcp source is missing.' }
$TempDir = Join-Path $Root 'build\temp'
New-Item -ItemType Directory -Force -Path $TempDir | Out-Null
$env:TEMP = $TempDir
$env:TMP = $TempDir
$env:DOTNET_CLI_HOME = Join-Path $Root 'build\dotnet-home'
$env:NUGET_PACKAGES = Join-Path $Root 'build\nuget-packages'
$env:NUGET_HTTP_CACHE_PATH = Join-Path $Root 'build\nuget-http-cache'
$env:NUGET_SCRATCH = Join-Path $Root 'build\nuget-scratch'
$env:NUGET_PLUGINS_CACHE_PATH = Join-Path $Root 'build\nuget-plugins-cache'
if ($SolidWorksDir -and -not $InteropDir) { $InteropDir = Join-Path $SolidWorksDir 'api\redist' }
$UsingPublicReferences = -not $InteropDir
if (-not $InteropDir) {
    $InteropDir = Join-Path $Root 'build\native-full\references'
    if (-not $SkipPrepare) {
        & $Python (Join-Path $Root 'scripts\prepare_full_executor.py')
        if ($LASTEXITCODE -ne 0) { throw 'Full executor reference preparation failed.' }
    }
}
foreach ($Name in @('sldworks', 'swconst', 'cosworks', 'fworks', 'sldcostingapi')) {
    if (-not (Test-Path (Join-Path $InteropDir "SolidWorks.Interop.$Name.dll"))) {
        throw "Full build requires SolidWorks.Interop.$Name.dll under $InteropDir; no tools will be omitted."
    }
}
New-Item -ItemType Directory -Force -Path $Destination | Out-Null
$Catalog = Join-Path $Destination 'tool-catalog.json'
if (Test-Path $Catalog) { Remove-Item $Catalog }
$Arguments = @('publish', $Source, '-c', 'Release', '-r', 'win-x64', '--self-contained', 'true', '-o', $Destination,
               '-p:PublishTrimmed=false', '-p:Version=0.1.0-forma-full.1', "-p:SolidWorksInteropDir=$InteropDir\")
$ShimTargets = Join-Path $Root 'build\native-full\FullExecutor.targets'
if ($UsingPublicReferences -and (Test-Path $ShimTargets)) {
    $Arguments += "-p:CustomAfterMicrosoftCommonTargets=$ShimTargets"
}
& $Dotnet @Arguments
if ($LASTEXITCODE -ne 0) { throw 'Full C# MCP publish failed; packaging stopped.' }
& $Python (Join-Path $Root 'scripts\verify_full_executor.py') --executable (Join-Path $Destination 'solidworks-mcp.exe') --output $Catalog
if ($LASTEXITCODE -ne 0) { throw 'The published executor does not match all 161 upstream tools; packaging stopped.' }
$Provenance = Join-Path $Root 'build\native-full\compile-references.json'
if ($UsingPublicReferences -and (Test-Path $Provenance)) {
    Copy-Item $Provenance $Destination -Force
    Copy-Item (Join-Path $Root 'build\native-full\reference-metadata') (Join-Path $Destination 'compile-reference-notices') -Recurse -Force
}
$Notices = Join-Path $Destination 'runtime-notices'
New-Item -ItemType Directory -Force -Path $Notices | Out-Null
foreach ($Notice in @('LICENSE.txt', 'ThirdPartyNotices.txt')) {
    $NoticeSource = Join-Path (Split-Path -Parent $Dotnet) $Notice
    if (Test-Path $NoticeSource) { Copy-Item $NoticeSource $Notices -Force }
}
Write-Host "Published and catalog-verified all 161 upstream tools: $Destination"
