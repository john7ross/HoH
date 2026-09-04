param(
    [Parameter(Mandatory = $true)]
    [string]$PythonSource,

    [string]$ProjectRoot = "",

    [switch]$BuildWheelhouse,

    [switch]$InstallOffline
)

$ErrorActionPreference = "Stop"

if ([string]::IsNullOrWhiteSpace($ProjectRoot)) {
    $ProjectRoot = Join-Path $PSScriptRoot ".."
}

$ProjectRoot = (Resolve-Path $ProjectRoot).Path
$source = Resolve-Path $PythonSource
$runtimeDir = Join-Path $ProjectRoot "runtime\python"
$wheelDir = Join-Path $ProjectRoot "vendor\wheels"
$targetPython = Join-Path $runtimeDir "python.exe"

if (-not (Test-Path (Join-Path $source "python.exe"))) {
    throw "PythonSource must point to a directory containing python.exe: $source"
}

New-Item -ItemType Directory -Force -Path $runtimeDir | Out-Null
New-Item -ItemType Directory -Force -Path $wheelDir | Out-Null

Copy-Item -Path (Join-Path $source "*") -Destination $runtimeDir -Recurse -Force

if (-not (Test-Path $targetPython)) {
    throw "Embedded python.exe was not created: $targetPython"
}

& $targetPython --version

if ($BuildWheelhouse) {
    & (Join-Path $PSScriptRoot "build-wheelhouse.ps1") -ProjectRoot $ProjectRoot -Python $targetPython
    if ($LASTEXITCODE -ne 0) {
        throw "Wheelhouse build failed with exit code $LASTEXITCODE"
    }
}
else {
    $wheels = Get-ChildItem -Path $wheelDir -Filter "*.whl" -ErrorAction SilentlyContinue
    if ($wheels.Count -eq 0) {
        Write-Warning "Wheelhouse is empty. Run scripts\build-wheelhouse.ps1 before production use."
    }
}

if ($InstallOffline) {
    & (Join-Path $PSScriptRoot "install-offline.ps1") -ProjectRoot $ProjectRoot -Python $targetPython
    if ($LASTEXITCODE -ne 0) {
        throw "Offline install failed with exit code $LASTEXITCODE"
    }
}

Write-Host "Embedded runtime ready: $targetPython"
Write-Host "Wheelhouse ready: $wheelDir"
