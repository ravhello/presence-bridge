# Presence Bridge

[![Validate](https://github.com/ravhello/presence-bridge/actions/workflows/validate.yml/badge.svg)](https://github.com/ravhello/presence-bridge/actions/workflows/validate.yml)
[![Release](https://img.shields.io/github/v/release/ravhello/presence-bridge?display_name=tag)](https://github.com/ravhello/presence-bridge/releases)

Presence Bridge adds room-level iPhone presence to Home Assistant without a
cloud account and without installing permanent Home Assistant credentials on
the phone.

It consists of:

- a free Home Assistant custom integration;
- one or more always-on Windows Bluetooth observers;
- the Presence Pair iPhone app, used once to establish a private Bluetooth
  identity with an observer.

After pairing, the Windows observer passively sees rotating Bluetooth private
addresses. Home Assistant resolves them locally and exposes a presence binary
sensor, a room sensor, and a device tracker for each linked person. IRKs,
Bluetooth addresses, and MQTT credentials never leave the local network.

## Requirements

- Home Assistant 2025.1 or newer with MQTT configured;
- Windows 10/11 with a Bluetooth LE adapter that supports peripheral mode;
- Python 3.11 or newer on each Windows observer;
- Presence Pair on an iPhone running iOS 17 or newer.

## Install Home Assistant

### HACS custom repository

1. In HACS, open **Integrations**, then the three-dot menu and
   **Custom repositories**.
2. Add `https://github.com/ravhello/presence-bridge` as an Integration.
3. Install **Presence Bridge** and restart Home Assistant.
4. Open **Settings > Devices & services > Add integration**, search for
   **Presence Bridge**, and complete setup.

For manual installation, copy `custom_components/presence_bridge` into the
matching directory under the Home Assistant configuration folder and restart.

## Install a Windows observer

Create a dedicated MQTT user first. On the Windows computer, open PowerShell as
Administrator in `bridge/windows` and run:

```powershell
Set-ExecutionPolicy -Scope Process Bypass
.\install.ps1
```

The installer asks for the observer name and MQTT credentials without placing
the password in shell history. It installs a SYSTEM startup task with automatic
restart and restricts the configuration directory to SYSTEM and Administrators.

Assign every fixed observer to its Home Assistant area in the Presence Bridge
panel. Room selection uses the observer with the strongest fresh signal.

## Pair an iPhone

1. Open **Presence Bridge** in the Home Assistant sidebar.
2. Select the person and the nearest Windows observer.
3. Tap **Create code**.
4. Open the code with Presence Pair or scan it in the app.
5. Keep the iPhone close to the observer and accept the iOS Bluetooth prompt.

The invitation expires after three minutes and can be used only for its active
session. See [Pairing protocol](docs/protocol.md) for the wire format and threat
model.

## Privacy

Presence Bridge is local-only. The app does not contain Home Assistant or MQTT
credentials, does not use analytics, and does not transmit location data. See
[Privacy](docs/privacy.md).

## Documentation

- [Windows observer](docs/windows-observer.md)
- [Pairing protocol](docs/protocol.md)
- [Privacy and security](docs/privacy.md)
- [Troubleshooting](docs/troubleshooting.md)
- [Italian tutorial](docs/tutorial-it.md)

## Status

The public protocol and integration are in preview. See the
[changelog](CHANGELOG.md) for release scope. Presence Pair App Store availability
is tracked in the project releases.

## License

Presence Bridge is released under the MIT License. Presence Pair is a separate
commercial application and is not covered by this repository's license.
