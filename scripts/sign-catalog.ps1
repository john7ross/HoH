param(
    [Parameter(Mandatory = $true)]
    [string]$Payload,
    [Parameter(Mandatory = $true)]
    [string]$Pfx,
    [string]$Output = "",
    [securestring]$Password,
    [string]$ProjectRoot = ""
)

$ErrorActionPreference = "Stop"
if ([string]::IsNullOrWhiteSpace($ProjectRoot)) {
    $ProjectRoot = Join-Path $PSScriptRoot ".."
}
$project = Resolve-Path $ProjectRoot
$payloadPath = Resolve-Path $Payload
$pfxPath = Resolve-Path $Pfx
if ([string]::IsNullOrWhiteSpace($Output)) {
    $Output = [IO.Path]::ChangeExtension($payloadPath.Path, ".signed.json")
}
if ($null -eq $Password) {
    $Password = Read-Host "Password for the private publisher PFX" -AsSecureString
}

$python = Join-Path $project "runtime\python\python.exe"
$sitePackages = Join-Path $project "runtime\site-packages"
if (-not (Test-Path $python)) {
    throw "Embedded Python is missing: $python"
}
$temporary = New-TemporaryFile
try {
    $canonicalizer = @'
import json, pathlib, sys
sys.path.insert(0, sys.argv[3])
from llm_harness.protocol import canonical_json_bytes
source = pathlib.Path(sys.argv[1])
target = pathlib.Path(sys.argv[2])
payload = json.loads(source.read_text(encoding="utf-8"))
if not isinstance(payload, dict):
    raise SystemExit("Catalog payload must be a JSON object.")
target.write_bytes(canonical_json_bytes(payload))
'@
    & $python -c $canonicalizer $payloadPath.Path $temporary.FullName $sitePackages
    if ($LASTEXITCODE -ne 0) {
        throw "Catalog canonicalization failed."
    }

    $certificate = [Security.Cryptography.X509Certificates.X509Certificate2]::new(
        $pfxPath.Path,
        $Password,
        [Security.Cryptography.X509Certificates.X509KeyStorageFlags]::EphemeralKeySet
    )
    if (-not $certificate.HasPrivateKey) {
        throw "The publisher PFX has no private key."
    }
    $rsa = [Security.Cryptography.X509Certificates.RSACertificateExtensions]::GetRSAPrivateKey($certificate)
    $signature = $rsa.SignData(
        [IO.File]::ReadAllBytes($temporary.FullName),
        [Security.Cryptography.HashAlgorithmName]::SHA256,
        [Security.Cryptography.RSASignaturePadding]::Pkcs1
    )
    $signatureText = [Convert]::ToBase64String($signature).TrimEnd("=").Replace("+", "-").Replace("/", "_")
    $envelope = [ordered]@{
        schema = "hoh.signed-catalog"
        version = "1.0"
        algorithm = "RS256"
        key_id = "sha256:" + $certificate.Thumbprint.ToLowerInvariant()
        payload = Get-Content -LiteralPath $payloadPath.Path -Raw -Encoding UTF8 | ConvertFrom-Json
        signature = $signatureText
    }
    [IO.File]::WriteAllText(
        $Output,
        ($envelope | ConvertTo-Json -Depth 20) + [Environment]::NewLine,
        [Text.UTF8Encoding]::new($false)
    )
    Write-Host "Signed catalog: $Output"
    Write-Host "Key id: $($envelope.key_id)"
}
finally {
    Remove-Item -LiteralPath $temporary.FullName -Force -ErrorAction SilentlyContinue
}
