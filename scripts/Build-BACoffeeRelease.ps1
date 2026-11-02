param(
    [Parameter(Mandatory=$true)][string]$ReleaseSource,
    [Parameter(Mandatory=$true)][string]$BuildRoot,
    [Parameter(Mandatory=$true)][string]$OutputDirectory,
    [string]$PythonPath=$env:BACOFFEE_BUILD_PYTHON,
    [string]$Version='v1.1.0'
)
$ErrorActionPreference='Stop'
if ([string]::IsNullOrWhiteSpace($PythonPath)) { $PythonPath='python' }
& $PythonPath (Join-Path $PSScriptRoot 'build_release.py') `
    --release-source $ReleaseSource --build-root $BuildRoot `
    --output-dir $OutputDirectory --version $Version
if ($LASTEXITCODE -ne 0) { throw "Release build failed: $LASTEXITCODE" }
