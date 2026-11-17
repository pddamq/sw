param(
    [string]$Destination = '',
    [switch]$CompileOnly
)
$ErrorActionPreference = 'Stop'
$Root = Split-Path -Parent $PSScriptRoot
if (-not $Destination) { $Destination = Join-Path $Root 'resources\builtin-mcp' }
$Compiler = Join-Path $env:WINDIR 'Microsoft.NET\Framework64\v4.0.30319\csc.exe'
if (-not (Test-Path $Compiler)) { throw 'Install .NET Framework 4.8 (x64) before building the built-in executor.' }
New-Item -ItemType Directory -Force -Path $Destination | Out-Null
$Output = Join-Path $Destination 'forma-mcp.exe'
& $Compiler /nologo /target:exe /platform:x64 /optimize+ /warnaserror+ /codepage:65001 /reference:System.Web.Extensions.dll /reference:System.Drawing.dll /reference:Microsoft.CSharp.dll "/out:$Output" (Join-Path $Root 'executor\FormaMcp.cs')
if ($LASTEXITCODE -ne 0) { throw 'Built-in C# executor compilation failed.' }
Copy-Item (Join-Path $Root 'executor\forma-mcp.exe.config') $Destination -Force
if (-not $CompileOnly) {
    $Version = & $Output --version
    if ($LASTEXITCODE -ne 0) { throw 'Built-in executor version check failed.' }
    Write-Host $Version
}
Write-Host "Built BASIC executor: $Output (18 tools; actual SOLIDWORKS validation is separate)."
