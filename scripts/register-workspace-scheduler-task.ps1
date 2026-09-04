param(
    [string]$DistributionRoot = "",
    [string]$TaskName = "HoH Workspace Scheduler",
    [int]$EveryMinutes = 1,
    [switch]$RunNow,
    [switch]$Unregister,
    [switch]$Status
)

$ErrorActionPreference = "Stop"

function Format-TaskArgument {
    param([string]$Value)
    if ($Value -match '[\s"]') {
        return '"' + $Value.Replace('"', '\"') + '"'
    }
    return $Value
}

if ([string]::IsNullOrWhiteSpace($DistributionRoot)) {
    $DistributionRoot = Join-Path $PSScriptRoot ".."
}
$distribution = Resolve-Path $DistributionRoot
$launcher = Join-Path $distribution "scripts\hoh.ps1"

if ($Status) {
    $existing = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
    if ($null -eq $existing) {
        Write-Host "installed=false"
        exit 0
    }
    $info = Get-ScheduledTaskInfo -TaskName $TaskName -ErrorAction Stop
    Write-Host "installed=true"
    Write-Host "state=$($existing.State)"
    Write-Host "last_run=$($info.LastRunTime.ToUniversalTime().ToString('o'))"
    Write-Host "last_result=$($info.LastTaskResult)"
    Write-Host "next_run=$($info.NextRunTime.ToUniversalTime().ToString('o'))"
    exit 0
}

if ($Unregister) {
    $existing = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
    if ($null -ne $existing) {
        Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false -ErrorAction Stop
    }
    Write-Host "installed=false"
    exit 0
}

if ($EveryMinutes -lt 1) {
    throw "EveryMinutes must be 1 or greater."
}
if (-not (Test-Path $launcher)) {
    throw "Portable launcher not found: $launcher"
}

$actionArgs = @(
    "-NoProfile",
    "-ExecutionPolicy",
    "Bypass",
    "-File",
    $launcher,
    "workspace",
    "run-due",
    "--json"
)
$action = New-ScheduledTaskAction `
    -Execute "powershell.exe" `
    -Argument (($actionArgs | ForEach-Object { Format-TaskArgument $_ }) -join " ") `
    -WorkingDirectory $distribution.Path
$trigger = New-ScheduledTaskTrigger `
    -Once `
    -At (Get-Date).AddMinutes(1) `
    -RepetitionInterval (New-TimeSpan -Minutes $EveryMinutes) `
    -RepetitionDuration (New-TimeSpan -Days 3650)
$settings = New-ScheduledTaskSettingsSet `
    -MultipleInstances IgnoreNew `
    -StartWhenAvailable `
    -ExecutionTimeLimit (New-TimeSpan -Hours 12)
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
    -Description "Runs due HoH project queues from the user-scoped workspace registry." `
    -Force | Out-Null

if ($RunNow) {
    Start-ScheduledTask -TaskName $TaskName
}
Write-Host "installed=true"
Write-Host "every_minutes=$EveryMinutes"
