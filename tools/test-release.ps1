param([Parameter(Mandatory)][string]$ReleaseDirectory)
$ErrorActionPreference = 'Stop'
$root = Split-Path $PSScriptRoot -Parent
$version = (Get-Content -LiteralPath (Join-Path $root 'custom_components/presence_bridge/manifest.json') -Raw | ConvertFrom-Json).version
Add-Type -AssemblyName System.IO.Compression.FileSystem
$packages = @(
    @{ Name = "presence-bridge-linux-$version.zip"; Source = '.'; Required = @('bridge/linux/receiver.py','bridge/linux/requirements.txt','bridge/linux/install.sh','bridge/linux/presence-bridge.service','bridge/linux/Dockerfile','bridge/linux/compose.yaml','custom_components/presence_bridge/protocol.py','custom_components/presence_bridge/receiver/engine.py','custom_components/presence_bridge/receiver/bluez.py','custom_components/presence_bridge/receiver/keys.py','docs/linux.md','LICENSE') },
    @{ Name = "presence_bridge-$version.zip"; Source = 'custom_components/presence_bridge'; Required = @('manifest.json','__init__.py','signal_api.py','signal_access.py','native_bluetooth.py','person_link.py','frontend/panel.js','frontend/panel-element.js','frontend/presence-pair-launch.js','strings.json','translations/en.json','translations/it.json') },
    @{ Name = "presence-bridge-windows-$version.zip"; Source = 'bridge/windows'; Required = @('observer.py','protocol.py','gatt_server.py','reverse_gatt_client.py','interactive_pairing_helper.py','numeric_pairing_probe.py','identity_removal.py','adapter_info.py','requirements.txt','install.ps1','uninstall.ps1','installer-access.ps1','config.example.json','README.md') }
)
foreach ($package in $packages) {
    $path = Join-Path $ReleaseDirectory $package.Name
    $zip = [IO.Compression.ZipFile]::OpenRead($path)
    try {
        $names = @($zip.Entries | ForEach-Object FullName)
        if (@($names | Sort-Object -Unique).Count -ne $names.Count) { throw 'Duplicate archive entries' }
        foreach ($required in $package.Required) {
            if ($required -notin $names) { throw "Missing $required from $($package.Name)" }
        }
        foreach ($entry in $zip.Entries) {
            if ($entry.FullName -match '(^/|\\|(^|/)\.\.(/|$)|(^|/)(config\.json|\.storage|__pycache__)(/|$)|\.(log|p8|pem|p12|pyc)$|(^|/)(Invoke-|Watch-))') {
                throw "Private or unsafe archive entry: $($entry.FullName)"
            }
            $source = Join-Path (Join-Path $root $package.Source) $entry.FullName
            $stream = $entry.Open()
            $hash = [Security.Cryptography.SHA256]::Create()
            try { $digest = [BitConverter]::ToString($hash.ComputeHash($stream)).Replace('-', '') }
            finally { $stream.Dispose(); $hash.Dispose() }
            if ($digest -ne (Get-FileHash -LiteralPath $source -Algorithm SHA256).Hash) {
                throw "Packaged file differs from tested source: $($entry.FullName)"
            }
            if ([IO.Path]::GetExtension($source) -eq '.ps1') {
                $tokens = $null; $errors = $null
                [System.Management.Automation.Language.Parser]::ParseFile($source, [ref]$tokens, [ref]$errors) | Out-Null
                if ($errors.Count) { throw "Invalid installer PowerShell: $($entry.FullName)" }
            }
        }
        Write-Output "PASS: $($package.Name), $($names.Count) files, source hashes and public contents verified"
    } finally { $zip.Dispose() }
}
