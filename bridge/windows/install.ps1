#Requires -Version 5.1
#Requires -RunAsAdministrator

[CmdletBinding()]
param(
    [string]$ObserverId,
    [string]$ObserverName,
    [string]$MqttHost,
    [ValidateRange(1, 65535)]
    [int]$MqttPort = 1883,
    [string]$MqttUsername,
    [string]$InstallRoot = "$env:ProgramData\PresenceBridge",
    [string]$TaskName = "Presence Bridge",
    [string]$LegacyGattTaskName = "Presence Bridge - GATT Host"
)

$ErrorActionPreference = 'Stop'
$sourceRoot = Split-Path -Parent $MyInvocation.MyCommand.Path

function Read-RequiredValue {
    param([string]$Value, [string]$Prompt)
    if (-not [string]::IsNullOrWhiteSpace($Value)) { return $Value.Trim() }
    do { $answer = Read-Host $Prompt } while ([string]::IsNullOrWhiteSpace($answer))
    return $answer.Trim()
}

function ConvertTo-PlainText {
    param([Security.SecureString]$Value)
    $pointer = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($Value)
    try { return [Runtime.InteropServices.Marshal]::PtrToStringBSTR($pointer) }
    finally { [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($pointer) }
}

$ObserverId = (Read-RequiredValue $ObserverId 'Observer ID (lowercase letters, numbers, underscores)').ToLowerInvariant()
if ($ObserverId -notmatch '^[a-z0-9_]{3,64}$') {
    throw 'ObserverId must contain 3-64 lowercase letters, numbers, or underscores.'
}
$ObserverName = Read-RequiredValue $ObserverName 'Observer display name'
$MqttHost = Read-RequiredValue $MqttHost 'MQTT host or IP'
$MqttUsername = Read-RequiredValue $MqttUsername 'MQTT username'
$mqttPassword = ConvertTo-PlainText (Read-Host 'MQTT password' -AsSecureString)

$python = Get-Command py.exe -ErrorAction SilentlyContinue
if ($python) {
    $pythonCommand = $python.Source
    $pythonArguments = @('-3')
} else {
    $python = Get-Command python.exe -ErrorAction SilentlyContinue
    if (-not $python) { throw 'Python 3.11 or newer is required.' }
    $pythonCommand = $python.Source
    $pythonArguments = @()
}

$existingTask = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
if ($existingTask -and $existingTask.State -eq 'Running') {
    Stop-ScheduledTask -TaskName $TaskName
    $deadline = (Get-Date).AddSeconds(15)
    do {
        Start-Sleep -Milliseconds 250
        $existingTask = Get-ScheduledTask -TaskName $TaskName
    } while ($existingTask.State -eq 'Running' -and (Get-Date) -lt $deadline)
    if ($existingTask.State -eq 'Running') {
        throw "The existing '$TaskName' task did not stop in time."
    }
}

# Protocol v2 uses Windows as the BLE central. Remove the v1 peripheral host
# because it is no longer part of pairing and can otherwise confuse diagnosis.
$legacyGattTask = Get-ScheduledTask -TaskName $LegacyGattTaskName -ErrorAction SilentlyContinue
if ($legacyGattTask) {
    Stop-ScheduledTask -TaskName $LegacyGattTaskName -ErrorAction SilentlyContinue
    Unregister-ScheduledTask -TaskName $LegacyGattTaskName -Confirm:$false
}
Get-AppxPackage -Name 'PresenceBridgeGattHost' -ErrorAction SilentlyContinue |
    Remove-AppxPackage -ErrorAction SilentlyContinue

New-Item -ItemType Directory -Path $InstallRoot -Force | Out-Null
$files = @(
    'adapter_info.py',
    'observer.py',
    'protocol.py',
    'reverse_gatt_client.py',
    'requirements.txt'
)
foreach ($file in $files) {
    Copy-Item -LiteralPath (Join-Path $sourceRoot $file) -Destination (Join-Path $InstallRoot $file) -Force
}

$venv = Join-Path $InstallRoot '.venv'
if (-not (Test-Path -LiteralPath (Join-Path $venv 'Scripts\python.exe'))) {
    & $pythonCommand @pythonArguments -m venv $venv
    if ($LASTEXITCODE -ne 0) { throw 'Unable to create the Python environment.' }
}
$venvPython = Join-Path $venv 'Scripts\python.exe'
& $venvPython -m pip install --disable-pip-version-check --upgrade pip
& $venvPython -m pip install --disable-pip-version-check -r (Join-Path $InstallRoot 'requirements.txt')
if ($LASTEXITCODE -ne 0) { throw 'Unable to install Presence Bridge dependencies.' }
$adapter = (& $venvPython (Join-Path $InstallRoot 'adapter_info.py') | ConvertFrom-Json)
if (-not $adapter.adapter_found -or -not $adapter.is_low_energy_supported -or -not $adapter.is_central_role_supported) {
    throw 'The Bluetooth adapter does not support the BLE central role required by Presence Bridge.'
}

$config = [ordered]@{
    observer_id = $ObserverId
    name = $ObserverName
    mqtt = [ordered]@{
        host = $MqttHost
        port = $MqttPort
        username = $MqttUsername
        password = $mqttPassword
    }
    publish_interval = 12
    observation_ttl = 55
    scanner_restart_interval = 1800
    scanner_stale_timeout = 120
    max_observations = 100
    app_pairing_enabled = $true
    log_path = (Join-Path $InstallRoot 'presence-bridge.log')
}
$configPath = Join-Path $InstallRoot 'config.json'
$config | ConvertTo-Json -Depth 4 | Set-Content -LiteralPath $configPath -Encoding UTF8
$mqttPassword = $null

$acl = Get-Acl -LiteralPath $InstallRoot
$acl.SetAccessRuleProtection($true, $false)
$inherit = [Security.AccessControl.InheritanceFlags]'ContainerInherit, ObjectInherit'
$propagation = [Security.AccessControl.PropagationFlags]::None
$acl.SetAccessRule((New-Object Security.AccessControl.FileSystemAccessRule('SYSTEM', 'FullControl', $inherit, $propagation, 'Allow')))
$acl.SetAccessRule((New-Object Security.AccessControl.FileSystemAccessRule('BUILTIN\Administrators', 'FullControl', $inherit, $propagation, 'Allow')))
Set-Acl -LiteralPath $InstallRoot -AclObject $acl

$action = New-ScheduledTaskAction -Execute $venvPython -Argument ('"{0}" --config "{1}"' -f (Join-Path $InstallRoot 'observer.py'), $configPath) -WorkingDirectory $InstallRoot
$trigger = New-ScheduledTaskTrigger -AtStartup
$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -RestartCount 999 -RestartInterval (New-TimeSpan -Minutes 1) -ExecutionTimeLimit ([TimeSpan]::Zero) -MultipleInstances IgnoreNew
$principal = New-ScheduledTaskPrincipal -UserId 'SYSTEM' -LogonType ServiceAccount -RunLevel Highest
$task = New-ScheduledTask -Action $action -Trigger $trigger -Settings $settings -Principal $principal -Description 'Local BLE presence observer and secure iPhone pairing bridge.'
Register-ScheduledTask -TaskName $TaskName -InputObject $task -Force | Out-Null
Start-ScheduledTask -TaskName $TaskName
Start-Sleep -Seconds 5
$state = (Get-ScheduledTask -TaskName $TaskName).State
if ($state -ne 'Running') { throw "Presence Bridge task state is $state." }

Write-Host "Presence Bridge installed at $InstallRoot" -ForegroundColor Green
Write-Host "Task: $TaskName ($state)" -ForegroundColor Green
Write-Host 'Add the Presence Bridge integration in Home Assistant, then assign this observer to an area.'
