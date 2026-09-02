---
title: Windows observer
permalink: /windows-observer/
---

# Windows observer

## Hardware check

Presence Pair needs a Bluetooth LE adapter that supports both central and
peripheral roles. Recent Intel adapters built into Windows 10/11 computers are
normally suitable. The installer does not alter unrelated Bluetooth devices.

## Installation

Run `install.ps1` from an elevated PowerShell. It:

1. copies the observer into `%ProgramData%\PresenceBridge`;
2. creates an isolated Python virtual environment;
3. installs pinned runtime packages;
4. verifies that the adapter can briefly host a GATT service;
5. writes the MQTT configuration with an administrator-only ACL;
6. creates a SYSTEM task that starts at boot and restarts after failures.

The observer is normally passive. It creates a connectable GATT advertisement
only after an HA administrator starts an app pairing session, and removes it on
completion, cancellation, timeout, or shutdown.

## Configuration

`config.example.json` lists every supported setting. `observer_id` is a stable
technical identifier. Do not change it after assigning the observer to an HA
area. `name` is the display name and may be changed safely.

Recommended MQTT ACL for observer `living_room_pc`:

```text
topic readwrite presence_bridge/v1/observers/living_room_pc/#
```

The legacy `smart_presence/ble` topic is optional compatibility behavior and may
be changed to an otherwise unused local prefix.

## Operations

```powershell
Get-ScheduledTask -TaskName 'Presence Bridge'
Get-Content "$env:ProgramData\PresenceBridge\presence-bridge.log" -Tail 100
Restart-ScheduledTask -TaskName 'Presence Bridge'
```

Re-run the installer to update in place while preserving the observer ID. To
remove it, run `uninstall.ps1` as Administrator. Add `-KeepConfiguration` to
retain the local settings and log.
