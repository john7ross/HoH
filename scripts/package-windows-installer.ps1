param(
    [string]$ProjectRoot = "",
    [string]$OutputDir = "",
    [string]$Iscc = ""
)

$ErrorActionPreference = "Stop"
if ([string]::IsNullOrWhiteSpace($ProjectRoot)) {
    $ProjectRoot = Join-Path $PSScriptRoot ".."
}
$project = (Resolve-Path $ProjectRoot).Path
if ([string]::IsNullOrWhiteSpace($OutputDir)) {
    $OutputDir = Join-Path $project "dist"
}
New-Item -ItemType Directory -Force -Path $OutputDir | Out-Null

if ([string]::IsNullOrWhiteSpace($Iscc)) {
    $isccCandidates = @(
        (Join-Path $env:LOCALAPPDATA "Programs\Inno Setup 6\ISCC.exe"),
        (Join-Path ${env:ProgramFiles(x86)} "Inno Setup 6\ISCC.exe"),
        (Join-Path $env:ProgramFiles "Inno Setup 6\ISCC.exe")
    )
    $Iscc = $isccCandidates | Where-Object { Test-Path -LiteralPath $_ } | Select-Object -First 1
}
if ([string]::IsNullOrWhiteSpace($Iscc) -or -not (Test-Path -LiteralPath $Iscc)) {
    throw "Inno Setup 6 compiler is missing. Install JRSoftware.InnoSetup with winget."
}

# package-deb.sh and package-macos.sh both refuse a dirty checkout. This one only
# wrote git_clean = false into the manifest and carried on, so the one artifact a
# Windows user installs was the one that could be built from unreviewed edits.
$dirty = @(& git -C $project status --porcelain)
if ($LASTEXITCODE -ne 0) {
    throw "Cannot determine whether the checkout is clean."
}
if ($dirty.Count -gt 0) {
    throw "Refusing to build a release from a dirty Git checkout: $($dirty.Count) path(s) differ."
}

$versionLine = Select-String -LiteralPath (Join-Path $project "pyproject.toml") -Pattern '^version\s*=\s*"([^"]+)"$'
if ($null -eq $versionLine -or $versionLine.Matches.Count -ne 1) {
    throw "Cannot determine the HoH version from pyproject.toml."
}
$version = $versionLine.Matches[0].Groups[1].Value
$internalDir = Join-Path $OutputDir "internal"
New-Item -ItemType Directory -Force -Path $internalDir | Out-Null
$payloadName = "HoH-update-payload-$version.zip"
$payloadManifestName = "HoH-update-payload-$version.manifest.json"

& (Join-Path $PSScriptRoot "build-update-payload.ps1") `
    -ProjectRoot $project `
    -OutputDir $internalDir `
    -PackageName $payloadName `
    -ManifestName $payloadManifestName
if ($LASTEXITCODE -ne 0) {
    throw "Internal update payload build failed."
}

$staging = Join-Path ([IO.Path]::GetTempPath()) ("hoh-windows-installer-" + [guid]::NewGuid().ToString("N"))
try {
    & (Join-Path $PSScriptRoot "install-update-payload.ps1") `
        -Package (Join-Path $internalDir $payloadName) `
        -Manifest (Join-Path $internalDir $payloadManifestName) `
        -InstallRoot $staging `
        -NoShortcut

    Add-Type -AssemblyName System.Drawing
    $iconPath = Join-Path $staging "hoh.ico"
    $bitmap = [Drawing.Bitmap]::new(256, 256)
    $graphics = [Drawing.Graphics]::FromImage($bitmap)
    $background = [Drawing.SolidBrush]::new([Drawing.Color]::FromArgb(13, 22, 41))
    $accent = [Drawing.SolidBrush]::new([Drawing.Color]::FromArgb(76, 117, 242))
    $foreground = [Drawing.SolidBrush]::new([Drawing.Color]::White)
    $font = [Drawing.Font]::new("Segoe UI", 132, [Drawing.FontStyle]::Bold, [Drawing.GraphicsUnit]::Pixel)
    try {
        $graphics.SmoothingMode = [Drawing.Drawing2D.SmoothingMode]::AntiAlias
        $graphics.FillRectangle($background, 0, 0, 256, 256)
        $graphics.FillRectangle($accent, 24, 24, 208, 208)
        $format = [Drawing.StringFormat]::new()
        $format.Alignment = [Drawing.StringAlignment]::Center
        $format.LineAlignment = [Drawing.StringAlignment]::Center
        try {
            $graphics.DrawString("H", $font, $foreground, [Drawing.RectangleF]::new(24, 20, 208, 212), $format)
            $handle = $bitmap.GetHicon()
            $icon = [Drawing.Icon]::FromHandle($handle)
            $stream = [IO.File]::Open($iconPath, [IO.FileMode]::Create)
            try { $icon.Save($stream) } finally { $stream.Dispose(); $icon.Dispose() }
        }
        finally { $format.Dispose() }
    }
    finally {
        $font.Dispose()
        $foreground.Dispose()
        $accent.Dispose()
        $background.Dispose()
        $graphics.Dispose()
        $bitmap.Dispose()
    }

    $iss = Join-Path $project "installer\windows\HoH.iss"
    & $Iscc "/DAppVersion=$version" "/DSourceDir=$staging" "/DOutputDir=$OutputDir" "/DAppIcon=$iconPath" $iss
    if ($LASTEXITCODE -ne 0) {
        throw "Inno Setup compilation failed."
    }

    $setupPath = Join-Path $OutputDir "HoH-Setup-$version.exe"
    if (-not (Test-Path -LiteralPath $setupPath)) {
        throw "Installer output is missing: $setupPath"
    }
    $commit = (& git -C $project rev-parse HEAD).Trim()
    # The gate at the top ran minutes ago and the payload was staged since. An edit
    # landing in between is both recorded as git_clean = false and, worse, could be
    # inside the artifact -- so the build fails rather than describing itself wrongly.
    $status = @(& git -C $project status --porcelain)
    if ($status.Count -gt 0) {
        throw "The checkout changed while the release was being built: $($status.Count) path(s) differ."
    }
    $setup = Get-Item -LiteralPath $setupPath
    $manifest = [ordered]@{
        schema = "hoh.windows-installer"
        manifest_version = "1.0"
        product = [ordered]@{ name = "HoH"; version = $version }
        source = [ordered]@{ commit = $commit; git_clean = ($status.Count -eq 0) }
        package = [ordered]@{
            name = $setup.Name
            bytes = $setup.Length
            sha256 = (Get-FileHash -LiteralPath $setup.FullName -Algorithm SHA256).Hash.ToLowerInvariant()
        }
        signing = [ordered]@{ authenticode = "unsigned"; update_catalog = "self-signed-rsa" }
    }
    $manifestPath = Join-Path $OutputDir "HoH-Setup-$version.manifest.json"
    [IO.File]::WriteAllText(
        $manifestPath,
        ($manifest | ConvertTo-Json -Depth 6) + [Environment]::NewLine,
        [Text.UTF8Encoding]::new($false)
    )
    Write-Host "Windows installer ready: $setupPath"
    Write-Host "Installer SHA-256: $($manifest.package.sha256)"
}
finally {
    if (Test-Path -LiteralPath $staging) {
        [IO.Directory]::Delete($staging, $true)
    }
}
