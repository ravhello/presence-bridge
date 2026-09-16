param([string]$OutputDirectory)
$ErrorActionPreference = 'Stop'
$root = Split-Path $PSScriptRoot -Parent
$manifest = Get-Content -LiteralPath (Join-Path $root 'custom_components\presence_bridge\manifest.json') -Raw | ConvertFrom-Json
if (-not $OutputDirectory) { $OutputDirectory = Join-Path $root ('build\release-' + $manifest.version) }
New-Item -ItemType Directory -Path $OutputDirectory -Force | Out-Null
Add-Type -AssemblyName System.IO.Compression.FileSystem
Add-Type -AssemblyName System.IO.Compression

function New-ReleaseArchive([string]$Name, [array]$Files, [string]$BasePath) {
    $path = Join-Path $OutputDirectory $Name
    if (Test-Path -LiteralPath $path) { throw "Archive already exists: $path" }
    $archive = [IO.Compression.ZipFile]::Open($path, [IO.Compression.ZipArchiveMode]::Create)
    try {
        foreach ($file in $Files) {
            $entry = $file.FullName.Substring($BasePath.Length).TrimStart('\').Replace('\', '/')
            [IO.Compression.ZipFileExtensions]::CreateEntryFromFile($archive, $file.FullName, $entry) | Out-Null
        }
    } finally { $archive.Dispose() }
    Get-FileHash -LiteralPath $path -Algorithm SHA256 | Select-Object Path,Hash
}

$componentRoot = Join-Path $root 'custom_components\presence_bridge'
$component = Get-ChildItem -LiteralPath $componentRoot -File -Recurse | Where-Object {
    $_.Extension -in '.py','.json','.yaml','.js','.svg','.png' -and $_.FullName -notmatch '__pycache__'
}
New-ReleaseArchive ('presence_bridge-' + $manifest.version + '.zip') $component $componentRoot

$windowsRoot = Join-Path $root 'bridge\windows'
# Explicit public payload: no live configuration, diagnostics, keys or traces.
$windowsFiles = @(
    'adapter_info.py','interactive_pairing_helper.py','numeric_pairing_probe.py',
    'identity_removal.py','gatt_server.py','observer.py','protocol.py',
    'reverse_gatt_client.py','requirements.txt','installer-access.ps1',
    'install.ps1','uninstall.ps1','config.example.json','README.md'
) | ForEach-Object { Get-Item -LiteralPath (Join-Path $windowsRoot $_) }
New-ReleaseArchive ('presence-bridge-windows-' + $manifest.version + '.zip') $windowsFiles $windowsRoot
$linuxFiles = @(
    'bridge/linux/receiver.py','bridge/linux/requirements.txt','bridge/linux/config.example.json',
    'bridge/linux/install.sh','bridge/linux/presence-bridge.service','bridge/linux/Dockerfile',
    'bridge/linux/compose.yaml','custom_components/presence_bridge/protocol.py',
    'docs/linux.md','docs/setup.md','docs/protocol.md','docs/compatibility.md','LICENSE','README.md'
) | ForEach-Object { Get-Item -LiteralPath (Join-Path $root $_) }
$linuxFiles += @(Get-ChildItem -LiteralPath (Join-Path $componentRoot 'receiver') -File -Filter '*.py')
New-ReleaseArchive ('presence-bridge-linux-' + $manifest.version + '.zip') $linuxFiles $root
& (Join-Path $PSScriptRoot 'test-release.ps1') -ReleaseDirectory $OutputDirectory
