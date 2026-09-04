param(
    [string]$Name = "HoH Local Publisher",
    [string]$OutputDir = "",
    [securestring]$Password
)

$ErrorActionPreference = "Stop"

if ($env:OS -ne "Windows_NT") {
    throw "Self-signed publisher generation currently requires Windows PowerShell/.NET certificate APIs."
}
if ([string]::IsNullOrWhiteSpace($OutputDir)) {
    $OutputDir = Join-Path $env:LOCALAPPDATA "HoH\publisher"
}
if ($null -eq $Password) {
    $Password = Read-Host "Password for the private publisher PFX" -AsSecureString
}

New-Item -ItemType Directory -Force -Path $OutputDir | Out-Null
$certificate = New-SelfSignedCertificate `
    -Subject "CN=$Name" `
    -FriendlyName $Name `
    -CertStoreLocation "Cert:\CurrentUser\My" `
    -KeyAlgorithm RSA `
    -KeyLength 3072 `
    -HashAlgorithm SHA256 `
    -KeyExportPolicy Exportable `
    -KeyUsage DigitalSignature `
    -NotAfter (Get-Date).AddYears(5)

$keyId = "sha256:" + $certificate.Thumbprint.ToLowerInvariant()
$pfxPath = Join-Path $OutputDir "hoh-publisher.pfx"
$publisherPath = Join-Path $OutputDir "hoh-publisher.public.json"
Export-PfxCertificate -Cert $certificate -FilePath $pfxPath -Password $Password | Out-Null

$rsa = [Security.Cryptography.X509Certificates.RSACertificateExtensions]::GetRSAPublicKey($certificate)
$parameters = $rsa.ExportParameters($false)
function ConvertTo-Base64Url([byte[]]$Bytes) {
    return [Convert]::ToBase64String($Bytes).TrimEnd("=").Replace("+", "-").Replace("/", "_")
}
$publisher = [ordered]@{
    key_id = $keyId
    name = $Name
    certificate_thumbprint = $certificate.Thumbprint
    rsa = [ordered]@{
        n = ConvertTo-Base64Url $parameters.Modulus
        e = ConvertTo-Base64Url $parameters.Exponent
    }
}
[IO.File]::WriteAllText(
    $publisherPath,
    ($publisher | ConvertTo-Json -Depth 5) + [Environment]::NewLine,
    [Text.UTF8Encoding]::new($false)
)

Write-Host "Private publisher identity: $pfxPath"
Write-Host "Public trust identity: $publisherPath"
Write-Host "Key id: $keyId"
Write-Host "Keep the PFX private. Distribute only the public JSON."
