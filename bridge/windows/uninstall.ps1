#Requires -Version 5.1
#Requires -RunAsAdministrator

[CmdletBinding()]
param(
    [string]$InstallRoot = "$env:ProgramData\PresenceBridge",
    [string]$TaskName = "Presence Bridge",
    [string]$PairingTaskName = "Presence Bridge - Interactive Pairing Client",
    [string]$GattTaskName = "Presence Bridge - GATT Host",
    [switch]$KeepConfiguration
)

$ErrorActionPreference = 'Stop'
$resolved = $null
if (Test-Path -LiteralPath $InstallRoot) {
    $rootItem = Get-Item -LiteralPath $InstallRoot -Force
    $resolved = $rootItem.FullName
    $programData = (Resolve-Path -LiteralPath $env:ProgramData).Path
    $prefix = $programData.TrimEnd('\') + '\'
    if (-not $rootItem.PSIsContainer -or
        ($rootItem.Attributes -band [IO.FileAttributes]::ReparsePoint) -or
        -not $resolved.StartsWith($prefix, [StringComparison]::OrdinalIgnoreCase) -or
        -not (Test-Path -LiteralPath (Join-Path $resolved 'observer.py')) -or
        -not (Test-Path -LiteralPath (Join-Path $resolved 'config.json'))) {
        throw 'Refusing to remove an unrecognized Presence Bridge installation.'
    }
    $config = Get-Content -LiteralPath (Join-Path $resolved 'config.json') -Raw | ConvertFrom-Json
    if ($config.observer_id -notmatch '^[a-z0-9_]{3,64}$') {
        throw 'The installation configuration is not a valid Presence Bridge observer.'
    }
}
$task = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
if ($task) {
    Stop-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
    Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
}
$gattTask = Get-ScheduledTask -TaskName $GattTaskName -ErrorAction SilentlyContinue
if ($gattTask) {
    Stop-ScheduledTask -TaskName $GattTaskName -ErrorAction SilentlyContinue
    Unregister-ScheduledTask -TaskName $GattTaskName -Confirm:$false
}
$pairingTask = Get-ScheduledTask -TaskName $PairingTaskName -ErrorAction SilentlyContinue
if ($pairingTask) {
    Stop-ScheduledTask -TaskName $PairingTaskName -ErrorAction SilentlyContinue
    Unregister-ScheduledTask -TaskName $PairingTaskName -Confirm:$false
}
Get-AppxPackage -Name 'PresenceBridgeGattHost' -ErrorAction SilentlyContinue |
    Remove-AppxPackage -ErrorAction SilentlyContinue
if ($resolved) {
    if ($KeepConfiguration) {
        Get-ChildItem -LiteralPath $resolved -Force |
            Where-Object Name -NotIn @('config.json', 'presence-bridge.log') |
            Remove-Item -Recurse -Force
    } else {
        Remove-Item -LiteralPath $resolved -Recurse -Force
    }
}
Write-Host 'Presence Bridge removed.' -ForegroundColor Green
