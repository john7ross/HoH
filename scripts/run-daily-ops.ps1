param(
    [string]$ProjectRoot = "",
    [string]$Config = "",
    [string]$StateRoot = "",
    [double]$TimeoutSeconds = 300,
    [int]$MaxTasks = 0,
    [double]$StaleMinutes = 60,
    [string[]]$FinalCheck = @(),
    [switch]$SkipDoctor,
    [switch]$SkipFinalAudit
)

$ErrorActionPreference = "Stop"

if ([string]::IsNullOrWhiteSpace($ProjectRoot)) {
    $ProjectRoot = Join-Path $PSScriptRoot ".."
}

$project = Resolve-Path $ProjectRoot
$launcher = Join-Path $project "scripts\hoh.ps1"

if ([string]::IsNullOrWhiteSpace($Config)) {
    $Config = Join-Path $project "harness.toml"
}
$configPath = Resolve-Path $Config

if (-not (Test-Path $launcher)) {
    throw "Portable launcher not found: $launcher"
}
if ($TimeoutSeconds -le 0) {
    throw "TimeoutSeconds must be greater than 0."
}
if ($MaxTasks -lt 0) {
    throw "MaxTasks must be 0 or greater."
}
if ($StaleMinutes -lt 0) {
    throw "StaleMinutes must be 0 or greater."
}

$doctorArgs = @(
    "--config",
    $configPath.Path,
    "--project-root",
    $project.Path
)

$loopCommonArgs = $doctorArgs

if (-not [string]::IsNullOrWhiteSpace($StateRoot)) {
    $resolvedStateRoot = $ExecutionContext.SessionState.Path.GetUnresolvedProviderPathFromPSPath($StateRoot)
    $loopCommonArgs += @("--state-root", $resolvedStateRoot)
}

if (-not $SkipDoctor) {
    Write-Host "HoH daily ops: doctor"
    & $launcher doctor @doctorArgs
    if ($LASTEXITCODE -ne 0) {
        throw "Doctor failed. Queue loop was not started."
    }
}

$loopArgs = @(
    "queue-run-loop"
) + $loopCommonArgs + @(
    "--timeout",
    [string]$TimeoutSeconds,
    "--max-tasks",
    [string]$MaxTasks,
    "--stale-minutes",
    [string]$StaleMinutes
)

if (($FinalCheck.Count -gt 0) -and (-not $SkipFinalAudit)) {
    $loopArgs += "--final-audit"
    foreach ($check in $FinalCheck) {
        if (-not [string]::IsNullOrWhiteSpace($check)) {
            $loopArgs += @("--final-check", $check)
        }
    }
}

Write-Host "HoH daily ops: queue-run-loop"
& $launcher @loopArgs
exit $LASTEXITCODE
