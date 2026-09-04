param(
    [Parameter(Mandatory = $true)]
    [string]$Package,
    [Parameter(Mandatory = $true)]
    [string]$Manifest,
    [string]$InstallRoot = "",
    [switch]$NoShortcut
)

$ErrorActionPreference = "Stop"
if ([string]::IsNullOrWhiteSpace($InstallRoot)) {
    if ($env:OS -eq "Windows_NT") {
        $InstallRoot = Join-Path $env:LOCALAPPDATA "Programs\HoH"
    }
    else {
        $InstallRoot = Join-Path $HOME ".local/share/hoh"
    }
}
$packagePath = (Resolve-Path $Package).Path
$manifestPath = (Resolve-Path $Manifest).Path
$manifestData = Get-Content -LiteralPath $manifestPath -Raw -Encoding UTF8 | ConvertFrom-Json
if ($manifestData.schema -ne "hoh.release-manifest" -or $manifestData.manifest_version -ne "1.0") {
    throw "Unsupported HoH release manifest."
}
$packageItem = Get-Item -LiteralPath $packagePath
$packageHash = (Get-FileHash -LiteralPath $packagePath -Algorithm SHA256).Hash.ToLowerInvariant()
if ($packageItem.Length -ne [long]$manifestData.package.bytes -or $packageHash -ne $manifestData.package.sha256) {
    throw "Package size or SHA-256 does not match the release manifest."
}
$version = [string]$manifestData.product.version
if ($version -notmatch '^\d+\.\d+\.\d+(?:-[0-9A-Za-z.-]+)?$') {
    throw "Release manifest has an unsafe product version."
}
$root = [IO.Path]::GetFullPath($InstallRoot)
$versions = Join-Path $root "versions"
$target = Join-Path $versions $version
if (Test-Path -LiteralPath $target) {
    throw "HoH $version is already installed at $target"
}
New-Item -ItemType Directory -Force -Path $versions | Out-Null
$staging = Join-Path $versions ("." + $version + "-" + [guid]::NewGuid().ToString("N"))
New-Item -ItemType Directory -Path $staging | Out-Null
try {
    Add-Type -AssemblyName System.IO.Compression.FileSystem
    $archive = [IO.Compression.ZipFile]::OpenRead($packagePath)
    try {
        $stagingFull = [IO.Path]::GetFullPath($staging) + [IO.Path]::DirectorySeparatorChar
        foreach ($entry in $archive.Entries) {
            $entryTarget = [IO.Path]::GetFullPath((Join-Path $staging $entry.FullName))
            if (-not $entryTarget.StartsWith($stagingFull, [StringComparison]::OrdinalIgnoreCase)) {
                throw "Release archive contains an unsafe path: $($entry.FullName)"
            }
        }
    }
    finally {
        $archive.Dispose()
    }
    Expand-Archive -LiteralPath $packagePath -DestinationPath $staging
    $receipt = [ordered]@{ schema = "hoh.installed-release"; version = $version; bytes = $packageItem.Length; sha256 = $packageHash }
    [IO.File]::WriteAllText(
        (Join-Path $staging ".hoh-release.json"),
        ($receipt | ConvertTo-Json) + [Environment]::NewLine,
        [Text.UTF8Encoding]::new($false)
    )
    Move-Item -LiteralPath $staging -Destination $target
    $staging = ""

    $currentPath = Join-Path $root "current.json"
    $previous = ""
    if (Test-Path -LiteralPath $currentPath) {
        $previousData = Get-Content -LiteralPath $currentPath -Raw -Encoding UTF8 | ConvertFrom-Json
        $previous = [string]$previousData.current
    }
    $pointer = [ordered]@{ schema = "hoh.current-release"; version = "1.0"; current = $version; previous = $previous }
    [IO.File]::WriteAllText(
        $currentPath,
        ($pointer | ConvertTo-Json) + [Environment]::NewLine,
        [Text.UTF8Encoding]::new($false)
    )

    $cliLauncher = Join-Path $root "launch-hoh.ps1"
    $guiRootLauncher = Join-Path $root "launch-hoh-gui.ps1"
    $cliContent = @'
param([Parameter(Position=0,ValueFromRemainingArguments=$true)][string[]]$HarnessArgs)
$ErrorActionPreference='Stop'
$pointer=Get-Content -LiteralPath (Join-Path $PSScriptRoot 'current.json') -Raw | ConvertFrom-Json
$launcher=Join-Path $PSScriptRoot ('versions\' + $pointer.current + '\scripts\hoh.ps1')
& $launcher @HarnessArgs
exit $LASTEXITCODE
'@
    $guiContent = @'
$ErrorActionPreference='Stop'
$pointer=Get-Content -LiteralPath (Join-Path $PSScriptRoot 'current.json') -Raw | ConvertFrom-Json
$launcher=Join-Path $PSScriptRoot ('versions\' + $pointer.current + '\scripts\hoh-gui.ps1')
& $launcher
'@
    [IO.File]::WriteAllText($cliLauncher, $cliContent + [Environment]::NewLine, [Text.UTF8Encoding]::new($false))
    [IO.File]::WriteAllText($guiRootLauncher, $guiContent + [Environment]::NewLine, [Text.UTF8Encoding]::new($false))

    if (-not $NoShortcut -and $env:OS -eq "Windows_NT") {
        $shortcutDir = Join-Path $env:APPDATA "Microsoft\Windows\Start Menu\Programs"
        $shortcutPath = Join-Path $shortcutDir "HoH.lnk"
        $shell = New-Object -ComObject WScript.Shell
        $shortcut = $shell.CreateShortcut($shortcutPath)
        $shortcut.TargetPath = (Get-Command powershell.exe).Source
        $shortcut.Arguments = "-NoProfile -ExecutionPolicy Bypass -File `"$guiRootLauncher`""
        $shortcut.WorkingDirectory = $root
        $shortcut.Save()
    }
    Write-Host "HoH $version installed at $target"
}
finally {
    if (-not [string]::IsNullOrWhiteSpace($staging) -and (Test-Path -LiteralPath $staging)) {
        Remove-Item -LiteralPath $staging -Recurse -Force
    }
}
