# Presence Bridge Windows receiver

This is a separate program required for initial Presence Pair iPhone
enrollment. Installing the HA integration alone does not install this receiver.
HA can run on another computer on the same local network.

## Before installation

- Windows with current security updates, Python 3.11+ for Windows.
- Bluetooth LE adapter supporting both central connections and peripheral
  advertising. Role support alone does not certify the driver or hardware.
- Local MQTT broker already connected to HA, with a dedicated receiver user.
- A signed-in Windows user for initial pairing; the session may be locked.
- Network access to download pinned Python dependencies during installation.

Open PowerShell as Administrator in this extracted directory and run:

```powershell
Set-ExecutionPolicy -Scope Process Bypass
.\install.ps1
```

The installer asks for MQTT details and creates startup/short-lived helper
tasks. Do not put passwords into pasted command lines or share `config.json`.
No UAC or Windows confirmation is needed for each phone enrollment. Passive
scanning starts as SYSTEM at boot; new pairing still needs the configured user
signed in. Do not enable automatic login to bypass that requirement.

In HA, open Presence Bridge, assign the receiver's actual room and create an
invitation for a person. The iPhone needs Bluetooth permission and must stay
near the receiver with Presence Pair open. Accept Pair if iOS asks; a valid
existing bond can be reused without another prompt. Verify completion in both
the app and HA. Do not manually reset working bonds before every attempt.

If HA is a VM on this PC, do not assign the same adapter exclusively to the VM.
The receiver communicates over MQTT, so it needs no Bluetooth passthrough.
The iPhone needs no Wi-Fi for Bluetooth pairing; opening HA on the same iPhone
or using the optional live-signal monitor does need network access to HA.

## Updates and support

Finish or cancel any active pairing before updating. Use the matching HA and
Windows release, keep the observer ID, and re-run `install.ps1`. Back up private
configuration first: the installer asks for credentials and writes defaults.
Use `uninstall.ps1` as administrator to remove the receiver.

- [Installation and troubleshooting](https://github.com/ravhello/presence-bridge)
- [Compatibility limits](https://github.com/ravhello/presence-bridge/blob/main/docs/compatibility.md)
- [Report an issue](https://github.com/ravhello/presence-bridge/issues)

Never attach identity keys, QR codes, broker credentials or HA backups to a
public issue. A running task or MQTT online status is not proof of a working
radio, authenticated enrollment or reliable room coverage.
