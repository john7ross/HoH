param(
    [Parameter(Position = 0, ValueFromRemainingArguments = $true)]
    [string[]]$HarnessArgs,
    [string]$ProjectRoot = "",
    [string]$Python = "",
    [switch]$AllowSourceFallback
)

$ErrorActionPreference = "Stop"

if ([string]::IsNullOrWhiteSpace($ProjectRoot)) {
    $ProjectRoot = Join-Path $PSScriptRoot ".."
}

$project = Resolve-Path $ProjectRoot
$runtimePython = Join-Path $project "runtime\python\python.exe"
$sitePackages = Join-Path $project "runtime\site-packages"
$sourcePath = Join-Path $project "src"

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

$pythonPathParts = @()
if (Test-Path $sitePackages) {
    $pythonPathParts += $sitePackages
}
elseif ($AllowSourceFallback -and (Test-Path $sourcePath)) {
    Write-Warning "Using source fallback. Run scripts\install-offline.ps1 for packaged execution."
    $pythonPathParts += $sourcePath
}
else {
    throw "Installed package directory not found: $sitePackages. Run scripts\install-offline.ps1."
}

if (-not [string]::IsNullOrWhiteSpace($env:PYTHONPATH)) {
    $pythonPathParts += $env:PYTHONPATH
}

# Tell the product where it is installed. Without it doctor resolves the
# embedded interpreter against the user's project and reports it missing.
$env:HOH_DISTRIBUTION_ROOT = $project
$env:PYTHONPATH = ($pythonPathParts -join [IO.Path]::PathSeparator)

& $Python -m llm_harness @HarnessArgs
exit $LASTEXITCODE
