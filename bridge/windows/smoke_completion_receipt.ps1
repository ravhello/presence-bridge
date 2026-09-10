[CmdletBinding()]
param([string]$InstallPath = 'C:\ProgramData\BlePresenceObserver')

$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'
$helperTask = 'Presence Bridge - Interactive Pairing Client'
$observerTask = 'Home Assistant - BLE Presence Observer'
$command = Join-Path $InstallPath 'interactive-pairing-command.json'
$result = Join-Path $InstallPath 'interactive-pairing-result.json'
if ((Get-ScheduledTask -TaskName $helperTask).State -eq 'Running' -or (Test-Path -LiteralPath $command)) {
    throw 'A pairing may be active. No test was started.'
}
$wasRunning = (Get-ScheduledTask -TaskName $observerTask).State -eq 'Running'
$sid = [Guid]::NewGuid().ToString('N')
$secret = New-Object byte[] 32
$rng = [Security.Cryptography.RandomNumberGenerator]::Create()
$rng.GetBytes($secret)
$rng.Dispose()
$encodedSecret = [Convert]::ToBase64String($secret).TrimEnd('=').Replace('+','-').Replace('/','_')
$expiry = [DateTimeOffset]::UtcNow.ToUnixTimeSeconds() + 60
$payload = @{
    schema = 1
    session_id = $sid
    transport = 'completion_beacon'
    pairing_uri = "presencepair://pair?v=2&sid=$sid&oid=smoke_receiver&exp=$expiry&secret=$encodedSecret"
    attempt_expires_at = $expiry
}
# This isolated UUID cannot match a real phone session and never creates a bond.
if (Test-Path -LiteralPath $result) {
    Copy-Item -LiteralPath $result -Destination (Join-Path $InstallPath 'before-receipt-smoke-result.json')
}
try {
    if ($wasRunning) { Stop-ScheduledTask -TaskName $observerTask }
    [IO.File]::WriteAllText($command, ($payload | ConvertTo-Json -Compress), (New-Object Text.UTF8Encoding($false)))
    Start-ScheduledTask -TaskName $helperTask
    $deadline = [DateTime]::UtcNow.AddSeconds(35)
    $advertised = $false
    do {
        Start-Sleep -Milliseconds 350
        if (-not (Test-Path -LiteralPath $result)) { continue }
        try { $status = Get-Content -LiteralPath $result -Raw | ConvertFrom-Json } catch { continue }
        if ($status.session_id -ne $sid) { continue }
        if ($status.detail_code -eq 'completion_beacon_advertising') { $advertised = $true }
        if ($status.state -eq 'error') { throw $status.message }
        if ($status.state -eq 'success') {
            if (-not $advertised) { throw 'Advertising transition was not observed' }
            Write-Output 'PASS: interactive Dell completion beacon advertised and stopped; no phone paired.'
            return
        }
    } while ([DateTime]::UtcNow -lt $deadline)
    throw 'Completion beacon did not finish within 35 seconds'
} finally {
    Stop-ScheduledTask -TaskName $helperTask
    if (Test-Path -LiteralPath $command) {
        $pending = Get-Content -LiteralPath $command -Raw | ConvertFrom-Json
        if ($pending.session_id -eq $sid) { Remove-Item -LiteralPath $command }
    }
    if ($wasRunning) { Start-ScheduledTask -TaskName $observerTask }
}
