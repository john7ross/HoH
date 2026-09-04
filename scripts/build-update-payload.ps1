param(
    [string]$ProjectRoot = "",
    [string]$OutputDir = "",
    [string]$PackageName = "HoH-update-payload.zip",
    [string]$ManifestName = "HoH-update-payload.manifest.json",
    [string]$ReleaseUrl = "",
    [string]$PublisherPfx = "",
    [securestring]$PublisherPassword,
    [switch]$SkipRefresh,
    [switch]$SkipDoctor,
    [switch]$SkipAudit
)

$ErrorActionPreference = "Stop"

if ([string]::IsNullOrWhiteSpace($ProjectRoot)) {
    $ProjectRoot = Join-Path $PSScriptRoot ".."
}

$project = Resolve-Path $ProjectRoot
if ([string]::IsNullOrWhiteSpace($OutputDir)) {
    $OutputDir = Join-Path $project "dist"
}

$runtimePython = Join-Path $project "runtime\python\python.exe"
$runtimeSitePackages = Join-Path $project "runtime\site-packages"
$wheelDir = Join-Path $project "vendor\wheels"
$launcher = Join-Path $project "scripts\hoh.ps1"
$lockFile = Join-Path $project "requirements.lock"

if (-not (Test-Path $runtimePython)) {
    throw "Embedded Python is missing: $runtimePython"
}
if (-not (Test-Path $runtimeSitePackages)) {
    throw "Offline installation is missing: $runtimeSitePackages"
}
if (-not (Test-Path $wheelDir)) {
    throw "Wheelhouse is missing: $wheelDir"
}
if ((Get-ChildItem -Path $wheelDir -Filter "*.whl").Count -eq 0) {
    throw "Wheelhouse contains no .whl files: $wheelDir"
}
if (-not (Test-Path $launcher)) {
    throw "Application launcher is missing: $launcher"
}
if (-not (Test-Path $lockFile)) {
    throw "Dependency lock file is missing: $lockFile"
}

if (-not $SkipRefresh) {
    & (Join-Path $PSScriptRoot "build-wheelhouse.ps1") -ProjectRoot $project -Python $runtimePython
    if ($LASTEXITCODE -ne 0) {
        throw "Wheelhouse refresh failed. Package was not created."
    }

    & (Join-Path $PSScriptRoot "install-offline.ps1") -ProjectRoot $project -Python $runtimePython
    if ($LASTEXITCODE -ne 0) {
        throw "Offline install refresh failed. Package was not created."
    }
}
$refreshGate = if ($SkipRefresh) { "skipped" } else { "passed" }

# Doctor runs against a stub configuration, the way package-deb.sh and
# package-macos.sh already do. Without --config it reads the product defaults,
# whose worker is the hermes_acp driver, so the release gate passed only on a
# machine where hermes happened to be installed and failed on every clean one,
# including CI. What a release gate must prove is that this build works, not
# which agents the builder has.
$doctorConfig = Join-Path ([System.IO.Path]::GetTempPath()) ("hoh-doctor-" + [System.Guid]::NewGuid().ToString("N") + ".toml")
@(
    "[runtime]"
    "require_embedded_python = false"
    "[worker]"
    'driver = "stub"'
    'command = "stub"'
    "args = []"
    "[[agents]]"
    'name = "release-smoke"'
    'command = "cmd"'
) | Set-Content -LiteralPath $doctorConfig -Encoding UTF8
try {
    if (-not $SkipDoctor) {
        & $launcher doctor --config $doctorConfig
        if ($LASTEXITCODE -ne 0) {
            throw "Doctor failed. Package was not created."
        }
    }
    $doctorGate = if ($SkipDoctor) { "skipped" } else { "passed" }

    if (-not $SkipAudit) {
        # The audit runs verification commands without a shell, so "set X=Y&&" cannot
        # work here. hoh.ps1 already points PYTHONPATH at runtime\site-packages, which
        # is the copy this payload ships -- the right thing for a release gate to test.
        $sourceTestCheck = "runtime\python\python.exe -m unittest discover -s tests"
        $compileCheck = "runtime\python\python.exe -m compileall -q src tests"
        $doctorCheck = "runtime\python\python.exe -m llm_harness doctor --config `"$doctorConfig`""
        & $launcher audit `
            --check $sourceTestCheck `
            --check $compileCheck `
            --check $doctorCheck `
            --markdown
        if ($LASTEXITCODE -ne 0) {
            throw "Audit failed. Package was not created."
        }
    }
    $auditGate = if ($SkipAudit) { "skipped" } else { "passed" }
}
finally {
    Remove-Item -LiteralPath $doctorConfig -Force -ErrorAction SilentlyContinue
}

New-Item -ItemType Directory -Force -Path $OutputDir | Out-Null
$packagePath = Join-Path $OutputDir $PackageName
if (Test-Path $packagePath) {
    Remove-Item -LiteralPath $packagePath -Force
}

$items = @(
    "ARCHITECTURE.md",
    "ARCHITECTURE.ru.md",
    "CONTRIBUTING.md",
    "CONTRIBUTING.ru.md",
    "THIRD-PARTY-NOTICES.md",
    "THIRD-PARTY-NOTICES.ru.md",
    "catalogs",
    "config.example.toml",
    "docs",
    "examples",
    "LICENSE",
    "NOTICE",
    "README.md",
    "README.ru.md",
    "RELEASE_NOTES.md",
    "RELEASE_NOTES.ru.md",
    "SECURITY.md",
    "SECURITY.ru.md",
    "requirements.lock",
    "runtime",
    "schemas",
    "scripts",
    "vendor"
)

$existingItems = @()
foreach ($item in $items) {
    $path = Join-Path $project $item
    if (Test-Path $path) {
        $existingItems += $path
    }
}

# Stage the payload so compiled bytecode can be dropped before it is archived.
# A .pyc records the absolute path it was compiled from, so shipping the builder's
# __pycache__ prints the maintainer's home directory in every user-visible traceback.
# Python regenerates them on the user's machine on first import.
$staging = Join-Path ([System.IO.Path]::GetTempPath()) ("hoh-payload-" + [System.Guid]::NewGuid().ToString("N"))
New-Item -ItemType Directory -Force -Path $staging | Out-Null
try {
    foreach ($path in $existingItems) {
        Copy-Item -LiteralPath $path -Destination $staging -Recurse -Force
    }
    Get-ChildItem -LiteralPath $staging -Recurse -Force -Directory -Filter "__pycache__" |
        Sort-Object -Property { $_.FullName.Length } -Descending |
        ForEach-Object { Remove-Item -LiteralPath $_.FullName -Recurse -Force }
    # The payload embeds CPython, Tcl/Tk, OpenSSL and others, each under its own
    # licence. The file that names them has to travel with the code it describes.
    foreach ($required in @("LICENSE", "NOTICE", "THIRD-PARTY-NOTICES.md", "THIRD-PARTY-NOTICES.ru.md")) {
        if (-not (Test-Path -LiteralPath (Join-Path $staging $required))) {
            throw "Payload is missing required file: $required"
        }
    }

    # Match on the extension rather than -Include. Under Windows PowerShell 5.1,
    # "-Recurse -Include *.pyc" also returns every .py file, so this gate reported
    # thousands of leftovers on a staging tree that held none and no release could
    # be built with the shell that ships with Windows.
    $leftovers = @(Get-ChildItem -LiteralPath $staging -Recurse -Force -File |
        Where-Object { $_.Extension -eq ".pyc" -or $_.Extension -eq ".pyo" })
    if ($leftovers.Count -gt 0) {
        throw "Refusing to ship compiled bytecode: $($leftovers.Count) file(s) remain under $staging."
    }

    # pip writes console-script launchers with the builder's interpreter path baked into
    # them, so runtime\site-packages\bin\llm-harness.exe pointed at a directory that only
    # exists on this machine. Nothing launches HoH through it -- every launcher runs
    # "python -m llm_harness" -- so it shipped a broken program and the maintainer's
    # folder layout for no gain.
    $scriptDir = Join-Path $staging "runtime\site-packages\bin"
    if (Test-Path -LiteralPath $scriptDir) {
        Remove-Item -LiteralPath $scriptDir -Recurse -Force
    }

    # Nothing a user receives should name the machine this was built on.
    $projectMarker = $project.ToString()
    $named = @(
        Get-ChildItem -LiteralPath $staging -Recurse -Force -File |
            Where-Object { $_.Length -le 2MB } |
            Where-Object {
                Select-String -LiteralPath $_.FullName -Pattern $projectMarker -SimpleMatch -Quiet -ErrorAction SilentlyContinue
            }
    )
    if ($named.Count -gt 0) {
        $sample = ($named | Select-Object -First 5 | ForEach-Object { $_.FullName.Substring($staging.Length) }) -join ", "
        throw "Refusing to ship the build machine's path: $($named.Count) file(s) name '$projectMarker' ($sample)."
    }

    Compress-Archive -Path (Join-Path $staging "*") -DestinationPath $packagePath -Force
}
finally {
    Remove-Item -LiteralPath $staging -Recurse -Force -ErrorAction SilentlyContinue
}

$gitCommit = (& git -C $project rev-parse HEAD).Trim()
if ($LASTEXITCODE -ne 0 -or [string]::IsNullOrWhiteSpace($gitCommit)) {
    throw "Cannot determine the source Git commit for the release manifest."
}
$gitCommitTime = (& git -C $project show -s --format=%cI HEAD).Trim()
if ($LASTEXITCODE -ne 0 -or [string]::IsNullOrWhiteSpace($gitCommitTime)) {
    throw "Cannot determine the source Git commit time for the release manifest."
}
$gitStatus = @(& git -C $project status --porcelain)
if ($LASTEXITCODE -ne 0) {
    throw "Cannot determine source Git cleanliness for the release manifest."
}

$runtimeVersion = (& $runtimePython --version 2>&1 | Out-String).Trim()
if ($LASTEXITCODE -ne 0 -or [string]::IsNullOrWhiteSpace($runtimeVersion)) {
    throw "Cannot determine the embedded Python version for the release manifest."
}
$versionProbe = "import sys; sys.path.insert(0, r'$runtimeSitePackages'); import llm_harness; print(llm_harness.__version__)"
$productVersion = (& $runtimePython -c $versionProbe 2>&1 | Out-String).Trim()
if ($LASTEXITCODE -ne 0 -or [string]::IsNullOrWhiteSpace($productVersion)) {
    throw "Cannot determine the installed HoH version for the release manifest."
}

$packageItem = Get-Item -LiteralPath $packagePath
$packageHash = (Get-FileHash -LiteralPath $packagePath -Algorithm SHA256).Hash.ToLowerInvariant()
$lockItem = Get-Item -LiteralPath $lockFile
$lockHash = (Get-FileHash -LiteralPath $lockFile -Algorithm SHA256).Hash.ToLowerInvariant()
$wheelRecords = @(
    Get-ChildItem -LiteralPath $wheelDir -Filter "*.whl" -File |
        Sort-Object Name |
        ForEach-Object {
            [ordered]@{
                name = $_.Name
                bytes = $_.Length
                sha256 = (Get-FileHash -LiteralPath $_.FullName -Algorithm SHA256).Hash.ToLowerInvariant()
            }
        }
)

$manifest = [ordered]@{
    schema = "hoh.release-manifest"
    manifest_version = "1.0"
    product = [ordered]@{
        name = "HoH"
        version = $productVersion
    }
    source = [ordered]@{
        commit = $gitCommit
        commit_time = $gitCommitTime
        git_clean = ($gitStatus.Count -eq 0)
    }
    target = [ordered]@{
        operating_system = [Environment]::OSVersion.VersionString
        architecture = $env:PROCESSOR_ARCHITECTURE
        runtime = $runtimeVersion
    }
    gates = [ordered]@{
        wheelhouse_refresh = $refreshGate
        doctor = $doctorGate
        audit = $auditGate
    }
    package = [ordered]@{
        name = $packageItem.Name
        bytes = $packageItem.Length
        sha256 = $packageHash
    }
    dependency_lock = [ordered]@{
        name = $lockItem.Name
        bytes = $lockItem.Length
        sha256 = $lockHash
    }
    wheels = $wheelRecords
}

$manifestPath = Join-Path $OutputDir $ManifestName
$manifestJson = $manifest | ConvertTo-Json -Depth 8
[System.IO.File]::WriteAllText(
    $manifestPath,
    $manifestJson + [Environment]::NewLine,
    [System.Text.UTF8Encoding]::new($false)
)

Write-Host "Internal update payload ready: $packagePath"
Write-Host "Release manifest ready: $manifestPath"
Write-Host "Package SHA-256: $packageHash"

if (-not [string]::IsNullOrWhiteSpace($ReleaseUrl)) {
    $platform = if ($env:PROCESSOR_ARCHITECTURE -match "ARM64") { "windows-aarch64" } else { "windows-x86_64" }
    $releasePayloadPath = Join-Path $OutputDir "HoH-release.payload.json"
    $releasePayload = [ordered]@{
        kind = "releases"
        generated_at_utc = [DateTimeOffset]::UtcNow.ToString("o")
        artifacts = @(
            [ordered]@{
                version = $productVersion
                platform = $platform
                url = $ReleaseUrl
                bytes = $packageItem.Length
                sha256 = $packageHash
                notes_url = ""
            }
        )
    }
    [IO.File]::WriteAllText(
        $releasePayloadPath,
        ($releasePayload | ConvertTo-Json -Depth 8) + [Environment]::NewLine,
        [Text.UTF8Encoding]::new($false)
    )
    Write-Host "Release catalog payload ready: $releasePayloadPath"
    if (-not [string]::IsNullOrWhiteSpace($PublisherPfx)) {
        $signArguments = @{
            Payload = $releasePayloadPath
            Pfx = $PublisherPfx
            Output = (Join-Path $OutputDir "HoH-releases.signed.json")
            ProjectRoot = $project
        }
        if ($null -ne $PublisherPassword) {
            $signArguments.Password = $PublisherPassword
        }
        & (Join-Path $PSScriptRoot "sign-catalog.ps1") @signArguments
        if ($LASTEXITCODE -ne 0) {
            throw "Release catalog signing failed."
        }
    }
}
elseif (-not [string]::IsNullOrWhiteSpace($PublisherPfx)) {
    throw "ReleaseUrl is required when PublisherPfx is supplied."
}
