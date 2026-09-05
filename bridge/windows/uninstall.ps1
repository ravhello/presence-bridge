#Requires -Version 5.1
#Requires -RunAsAdministrator

[CmdletBinding()]
param(
    [string]$InstallRoot = "$env:ProgramData\PresenceBridge",
    [string]$TaskName = "Presence Bridge",
    [string]$GattTaskName = "Presence Bridge - GATT Host",
    [switch]$KeepConfiguration
)

$ErrorActionPreference = 'Stop'
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
Get-AppxPackage -Name 'PresenceBridgeGattHost' -ErrorAction SilentlyContinue |
    Remove-AppxPackage -ErrorAction SilentlyContinue
if (Test-Path -LiteralPath $InstallRoot) {
    if ($KeepConfiguration) {
        Get-ChildItem -LiteralPath $InstallRoot -Force |
            Where-Object Name -NotIn @('config.json', 'presence-bridge.log') |
            Remove-Item -Recurse -Force
    } else {
        $resolved = (Resolve-Path -LiteralPath $InstallRoot).Path
        $programData = (Resolve-Path -LiteralPath $env:ProgramData).Path
        $prefix = $programData.TrimEnd('\') + '\'
        if (-not $resolved.StartsWith($prefix, [StringComparison]::OrdinalIgnoreCase)) {
            throw 'Refusing to remove a directory outside ProgramData.'
        }
        Remove-Item -LiteralPath $resolved -Recurse -Force
    }
}
Write-Host 'Presence Bridge removed.' -ForegroundColor Green
