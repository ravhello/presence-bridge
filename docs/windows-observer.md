---
title: Windows observer
permalink: /windows-observer/
---

# Windows observer

## Hardware check

Presence Pair needs a Bluetooth LE adapter that supports both central
connections and peripheral advertising. Windows uses advertising for the
proximity check and the final receipt; central support alone is not enough.
The installer checks both reported capabilities. Driver and radio behavior
still need a real-device test; not every adapter has been validated.
Use a Windows version receiving security updates. The installer does not alter
unrelated Bluetooth devices.

## Installation

Run `install.ps1` from an elevated PowerShell. It:

1. copies the observer into `%ProgramData%\PresenceBridge`;
2. creates an isolated Python virtual environment;
3. installs pinned runtime packages;
4. protects executable files against writes from the interactive pairing user;
5. creates a SYSTEM task that starts at boot and restarts after failures;
6. creates an on-demand helper task for the configured signed-in Windows user;
7. removes the obsolete protocol-v1 GATT-host task and sparse package.

The selected Windows user must remain signed in during enrollment. A locked
session is sufficient; signing out prevents that task from running. The SYSTEM
observer keeps working before login for phones already enrolled. Do not enable
automatic Windows login just for this integration.

Code and configuration are readable by the selected pairing user but writable
only by SYSTEM and Administrators. SYSTEM command files remain in that protected
root; the interactive user can only read them. Mutable helper results/logs live under
`%ProgramData%\PresenceBridge\pairing`, with access restricted to those accounts.

The observer is normally passive. During enrollment it pauses the presence
scan, advertises a session-specific proximity beacon, then searches for the
temporary service advertised by the iPhone, checks the one-time session, and
only then starts or reuses the encrypted bond. No Windows
desktop confirmation is required; iOS may show a standard Pair request.
The iPhone completes only after verifying Home Assistant's saved-identity
receipt. The five-minute attempt includes proximity and retries; a saved bond
may complete without another Pair popup.

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
Get-ScheduledTask -TaskName 'Presence Bridge - Interactive Pairing Client'
```

For a deliberate observer restart when no pairing is active:

```powershell
Stop-ScheduledTask -TaskName 'Presence Bridge'
Start-ScheduledTask -TaskName 'Presence Bridge'
```

Re-run the installer to update in place. It stops the previous task before
replacing files and verifies that the observer stays running. Keep the same
observer ID so Home Assistant retains the room assignment. To remove the bridge,
run `uninstall.ps1` as Administrator. Add `-KeepConfiguration` to retain the
local settings and log. Updating refuses to interrupt an active pairing task.
Retain a private backup of `config.json` if you customized advanced settings;
the installer asks for connection details and writes the supported defaults.
