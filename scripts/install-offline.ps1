param(
    [string]$ProjectRoot = "",
    [string]$Python = "",
    [string]$PackageName = "llm-harness",
    [string]$RequirementsLock = "requirements.lock"
)

$ErrorActionPreference = "Stop"

if ([string]::IsNullOrWhiteSpace($ProjectRoot)) {
    $ProjectRoot = Join-Path $PSScriptRoot ".."
}

$project = Resolve-Path $ProjectRoot
$runtimePython = Join-Path $project "runtime\python\python.exe"
$wheelDir = Join-Path $project "vendor\wheels"
$targetDir = Join-Path $project "runtime\site-packages"
$lockPath = Join-Path $project $RequirementsLock

if ([string]::IsNullOrWhiteSpace($Python)) {
    $Python = $runtimePython
}

if (-not (Test-Path $Python)) {
    $pythonCommand = Get-Command $Python -ErrorAction SilentlyContinue
    if ($null -eq $pythonCommand) {
        throw "Python executable not found: $Python"
    }
    $Python = $pythonCommand.Source
}

if (-not (Test-Path $wheelDir)) {
    throw "Wheelhouse directory not found: $wheelDir"
}

$wheels = Get-ChildItem -Path $wheelDir -Filter "*.whl"
if ($wheels.Count -eq 0) {
    throw "Wheelhouse is empty: $wheelDir"
}
if (-not (Test-Path $lockPath)) {
    throw "Dependency lock file not found: $lockPath"
}

New-Item -ItemType Directory -Force -Path $targetDir | Out-Null
$resolvedTargetDir = (Resolve-Path $targetDir).Path
$resolvedProject = $project.Path
if (-not $resolvedTargetDir.StartsWith(
    $resolvedProject + [IO.Path]::DirectorySeparatorChar,
    [StringComparison]::OrdinalIgnoreCase
)) {
    throw "Refusing to refresh package files outside the project: $resolvedTargetDir"
}

# Remove only the previously installed HoH package and its version metadata. This prevents an old
# dist-info directory from being archived beside the current version.
Get-ChildItem -LiteralPath $resolvedTargetDir -Force | Where-Object {
    $_.Name -eq "llm_harness" -or $_.Name -like "llm_harness-*.dist-info"
} | Remove-Item -Recurse -Force

$lockedRequirements = Get-Content -Path $lockPath | Where-Object {
    $line = $_.Trim()
    -not [string]::IsNullOrWhiteSpace($line) -and -not $line.StartsWith("#")
}

if ($lockedRequirements.Count -gt 0) {
    & $Python -m pip install `
        --no-index `
        --find-links $wheelDir `
        --target $targetDir `
        --upgrade `
        --no-deps `
        --requirement $lockPath

    if ($LASTEXITCODE -ne 0) {
        throw "Offline dependency install failed with exit code $LASTEXITCODE"
    }
}

& $Python -m pip install `
    --no-index `
    --find-links $wheelDir `
    --target $targetDir `
    --upgrade `
    --no-deps `
    $PackageName

if ($LASTEXITCODE -ne 0) {
    throw "Offline install failed with exit code $LASTEXITCODE"
}

Write-Host "Offline package install ready: $targetDir"
