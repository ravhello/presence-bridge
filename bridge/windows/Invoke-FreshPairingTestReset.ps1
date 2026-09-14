param(
    [Parameter(Mandatory=$true)][string]$ExpectedHost,
    [Parameter(Mandatory=$true)][string]$ObserverId,
    [Parameter(Mandatory=$true)][ValidatePattern('^[0-9A-Fa-f]{12}$')][string]$AnchorAddress,
    [Parameter(Mandatory=$true)][Guid]$ContainerId
)

$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'
if ($env:COMPUTERNAME -ine $ExpectedHost) { throw 'Wrong host; no changes made' }
if ($ObserverId -notmatch '^[a-z0-9_]+$') { throw 'Invalid receiver ID' }
$root = $PSScriptRoot
$observerTask = 'Home Assistant - BLE Presence Observer'
$pairingTask = 'Presence Bridge - Interactive Pairing Client'
$idleDeadline = [DateTime]::UtcNow.AddSeconds(10)
while ((Get-ScheduledTask -TaskName $pairingTask).State -eq 'Running' -and [DateTime]::UtcNow -lt $idleDeadline) {
    Start-Sleep -Milliseconds 250
}
if ((Get-ScheduledTask -TaskName $pairingTask).State -eq 'Running') {
    throw 'Pairing is active; cancel it in HA before requesting a fresh test'
}
$wasRunning = (Get-ScheduledTask -TaskName $observerTask).State -eq 'Running'
$request = [Guid]::NewGuid().ToString()
$taskName = "Presence Bridge - Fresh Test $request"
$output = Join-Path $root "fresh-test-reset-$request.json"
$python = Join-Path $root '.venv\Scripts\pythonw.exe'
$script = Join-Path $root 'fresh_pairing_test.py'
$config = Join-Path $root 'config.json'
foreach ($path in @($python, $script, $config)) {
    if (-not (Test-Path -LiteralPath $path -PathType Leaf)) { throw "Missing prerequisite: $path" }
}
$registered = $false
$started = [DateTimeOffset]::UtcNow.ToUnixTimeSeconds()
$lock = [IO.File]::Open((Join-Path $root 'fresh-test-reset.lock'), [IO.FileMode]::OpenOrCreate, [IO.FileAccess]::ReadWrite, [IO.FileShare]::None)
try {
    if ($wasRunning) { Stop-ScheduledTask -TaskName $observerTask }
    $arguments = '"{0}" --config "{1}" --expected-host "{2}" --observer-id "{3}" --anchor-address {4} --container-id {5} --request-id {6} --result "{7}"' -f $script,$config,$ExpectedHost,$ObserverId,$AnchorAddress,$ContainerId,$request,$output
    $action = New-ScheduledTaskAction -Execute $python -Argument $arguments -WorkingDirectory $root
    $settings = New-ScheduledTaskSettingsSet -Hidden -ExecutionTimeLimit (New-TimeSpan -Seconds 90) -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries
    Register-ScheduledTask -TaskName $taskName -Action $action -Settings $settings -User 'SYSTEM' -RunLevel Highest | Out-Null
    $registered = $true
    Start-ScheduledTask -TaskName $taskName
    $deadline = [DateTime]::UtcNow.AddSeconds(100)
    $result = $null
    while ([DateTime]::UtcNow -lt $deadline) {
        if (Test-Path -LiteralPath $output) {
            try { $result = Get-Content -LiteralPath $output -Raw | ConvertFrom-Json } catch { $result = $null }
            if ($null -ne $result) { break }
        }
        Start-Sleep -Milliseconds 500
    }
    if ($null -eq $result) { throw 'Fresh test reset timed out; no QR permitted' }
    if ($result.request_id -ne $request -or $result.host -ine $ExpectedHost -or $result.observer_id -ne $ObserverId) {
        throw 'Reset receipt does not match this request'
    }
    if ($result.clean -ne $true -or $result.completed_at -lt $started -or $result.bonds_remaining -ne 0 -or $result.keys_remaining -ne 0 -or $result.unrelated_preserved -ne $true) {
        throw "Reset not verified: $($result.error)"
    }
    $result | ConvertTo-Json -Compress
} finally {
    try {
        if ($registered) {
            Stop-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue
            Unregister-ScheduledTask -TaskName $taskName -Confirm:$false
        }
    } finally {
        try {
            if ($wasRunning) { Start-ScheduledTask -TaskName $observerTask }
        } finally {
            $lock.Dispose()
        }
    }
}
