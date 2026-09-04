param(
    [string]$ProjectRoot = "",
    [string]$Python = "python",
    [string]$RequirementsLock = "requirements.lock"
)

$ErrorActionPreference = "Stop"

if ([string]::IsNullOrWhiteSpace($ProjectRoot)) {
    $ProjectRoot = Join-Path $PSScriptRoot ".."
}

$project = Resolve-Path $ProjectRoot
$wheelDir = Join-Path $project "vendor\wheels"
$lockPath = Join-Path $project $RequirementsLock

New-Item -ItemType Directory -Force -Path $wheelDir | Out-Null

# Keep exactly one project wheel so a portable refresh cannot install or ship a stale HoH version.
Get-ChildItem -LiteralPath $wheelDir -Filter "llm_harness-*.whl" -File |
    Remove-Item -Force

$arguments = @("-m", "pip", "wheel", ".", "--wheel-dir", $wheelDir, "--no-deps")

Push-Location $project
try {
    & $Python @arguments
    if ($LASTEXITCODE -ne 0) {
        throw "pip wheel failed with exit code $LASTEXITCODE"
    }

    if (Test-Path $lockPath) {
        $lockedRequirements = Get-Content -Path $lockPath | Where-Object {
            $line = $_.Trim()
            -not [string]::IsNullOrWhiteSpace($line) -and -not $line.StartsWith("#")
        }
        if ($lockedRequirements.Count -gt 0) {
            $lockArguments = @("-m", "pip", "wheel", "--requirement", $lockPath, "--wheel-dir", $wheelDir, "--no-deps")
            & $Python @lockArguments
            if ($LASTEXITCODE -ne 0) {
                throw "pip wheel from $RequirementsLock failed with exit code $LASTEXITCODE"
            }
        }
    }
    else {
        throw "Dependency lock file not found: $lockPath"
    }
}
finally {
    Pop-Location
}

$wheels = Get-ChildItem -Path $wheelDir -Filter "*.whl"
if ($wheels.Count -eq 0) {
    throw "Wheelhouse is empty: $wheelDir"
}

Write-Host "Wheelhouse ready: $wheelDir"
foreach ($wheel in $wheels) {
    Write-Host "  $($wheel.Name)"
}
