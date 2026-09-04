param(
    [string]$ProjectRoot = "",
    [string]$Config = "",
    [string]$StateRoot = "",
    [string]$TaskName = "HoH Telegram Watch",
    [int]$EveryMinutes = 1,
    [int]$Iterations = 1,
    [int]$PollTimeoutSeconds = 30,
    [double]$IntervalSeconds = 0,
    [switch]$RunNow,
    [switch]$Unregister
)

$ErrorActionPreference = "Stop"

function Format-TaskArgument {
    param([string]$Value)

    if ($Value -match '[\s"]') {
        return '"' + $Value.Replace('"', '\"') + '"'
    }
    return $Value
}

if ([string]::IsNullOrWhiteSpace($ProjectRoot)) {
    $ProjectRoot = Join-Path $PSScriptRoot ".."
}

$project = Resolve-Path $ProjectRoot
$launcher = Join-Path $project "scripts\hoh.ps1"

if ($Unregister) {
    $existingTask = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
    if ($null -eq $existingTask) {
        Write-Host "Scheduled task not found: $TaskName"
        exit 0
    }
    Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false -ErrorAction Stop
    Write-Host "Scheduled task removed: $TaskName"
    exit 0
}

if ([string]::IsNullOrWhiteSpace($Config)) {
    $Config = Join-Path $project "harness.toml"
}
$configPath = Resolve-Path $Config

if ($EveryMinutes -lt 1) {
    throw "EveryMinutes must be 1 or greater."
}
if ($Iterations -lt 1) {
    throw "Iterations must be 1 or greater for scheduled runs. Use scripts\hoh.ps1 telegram-watch --iterations 0 for a manual continuous session."
}
if ($PollTimeoutSeconds -lt 0) {
    throw "PollTimeoutSeconds must be 0 or greater."
}
if ($IntervalSeconds -lt 0) {
    throw "IntervalSeconds must be 0 or greater."
}
if (-not (Test-Path $launcher)) {
    throw "Portable launcher not found: $launcher"
}

$harnessArgs = @(
    "telegram-watch",
    "--config",
    $configPath.Path,
    "--project-root",
    $project.Path,
    "--iterations",
    [string]$Iterations,
    "--timeout",
    [string]$PollTimeoutSeconds,
    "--interval-seconds",
    [string]$IntervalSeconds
)

if (-not [string]::IsNullOrWhiteSpace($StateRoot)) {
    $resolvedStateRoot = $ExecutionContext.SessionState.Path.GetUnresolvedProviderPathFromPSPath($StateRoot)
    $harnessArgs += @("--state-root", $resolvedStateRoot)
}

$actionArgs = @(
    "-NoProfile",
    "-ExecutionPolicy",
    "Bypass",
    "-File",
    $launcher
) + $harnessArgs

$action = New-ScheduledTaskAction `
    -Execute "powershell.exe" `
    -Argument (($actionArgs | ForEach-Object { Format-TaskArgument $_ }) -join " ") `
    -WorkingDirectory $project.Path

$trigger = New-ScheduledTaskTrigger `
    -Once `
    -At (Get-Date).AddMinutes(1) `
    -RepetitionInterval (New-TimeSpan -Minutes $EveryMinutes) `
    -RepetitionDuration (New-TimeSpan -Days 3650)

$settings = New-ScheduledTaskSettingsSet `
    -MultipleInstances IgnoreNew `
    -StartWhenAvailable `
    -ExecutionTimeLimit (New-TimeSpan -Minutes ([Math]::Max(5, $EveryMinutes * 2)))

$principal = New-ScheduledTaskPrincipal `
    -UserId ([System.Security.Principal.WindowsIdentity]::GetCurrent().Name) `
    -LogonType Interactive `
    -RunLevel LeastPrivilege

Register-ScheduledTask `
    -TaskName $TaskName `
    -Action $action `
    -Trigger $trigger `
    -Settings $settings `
    -Principal $principal `
    -Description "HoH Telegram operator command polling." `
    -Force | Out-Null

if ($RunNow) {
    Start-ScheduledTask -TaskName $TaskName
}

Write-Host "Scheduled task registered: $TaskName"
Write-Host "Project root: $($project.Path)"
Write-Host "Config: $($configPath.Path)"
Write-Host "Every minutes: $EveryMinutes"
Write-Host "Iterations per run: $Iterations"
