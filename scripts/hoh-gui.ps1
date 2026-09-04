param(
    [string]$ProjectRoot = "",
    [string]$Python = "",
    [switch]$AllowSourceFallback
)

$ErrorActionPreference = "Stop"
$distributionRoot = Resolve-Path (Join-Path $PSScriptRoot "..")
$runtimePythonw = Join-Path $distributionRoot "runtime\python\pythonw.exe"
$sitePackages = Join-Path $distributionRoot "runtime\site-packages"
$sourcePath = Join-Path $distributionRoot "src"

if ([string]::IsNullOrWhiteSpace($Python)) {
    $Python = $runtimePythonw
}
if (-not (Test-Path -LiteralPath $Python)) {
    throw "Python GUI executable not found: $Python"
}

$pythonPathParts = @()
if (Test-Path -LiteralPath $sitePackages) {
    $pythonPathParts += $sitePackages
}
elseif ($AllowSourceFallback -and (Test-Path -LiteralPath $sourcePath)) {
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
$env:HOH_DISTRIBUTION_ROOT = $distributionRoot
$env:PYTHONPATH = ($pythonPathParts -join [IO.Path]::PathSeparator)

$arguments = @("-m", "llm_harness", "gui")
if (-not [string]::IsNullOrWhiteSpace($ProjectRoot)) {
    $arguments += @("--project-root", (Resolve-Path $ProjectRoot))
}

Start-Process -FilePath $Python -ArgumentList $arguments -WindowStyle Hidden
