---
title: Windows observer
permalink: /windows-observer/
---

# Windows observer

## Hardware check

Presence Pair needs a Bluetooth LE adapter that supports active scanning and
central connections. Recent Intel adapters built into Windows 10/11 computers
are normally suitable. The installer does not alter unrelated Bluetooth devices.

## Installation

Run `install.ps1` from an elevated PowerShell. It:

1. copies the observer into `%ProgramData%\PresenceBridge`;
2. creates an isolated Python virtual environment;
3. installs pinned runtime packages;
4. writes the MQTT configuration with an administrator-only ACL;
5. creates a SYSTEM task that starts at boot and restarts after failures;
6. removes the obsolete protocol-v1 GATT-host task and sparse package.

The observer is normally passive. During enrollment it pauses the presence
scan, searches for the temporary service advertised by the iPhone, checks the
plain one-time session, and only then starts the encrypted bond. No Windows
desktop confirmation is required; iOS may show a standard Pair request.

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

Re-run the installer to update in place. It stops the previous task before
replacing files and verifies that the observer stays running. Keep the same
observer ID so Home Assistant retains the room assignment. To remove the bridge,
run `uninstall.ps1` as Administrator. Add `-KeepConfiguration` to retain the
local settings and log.
