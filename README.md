# Presence Bridge

**Public preview 0.1.36** is available as a free
[GitHub pre-release](https://github.com/ravhello/presence-bridge/releases/tag/v0.1.36).
Install it through a HACS custom repository with pre-releases enabled, or use
the manual package. This is not a default HACS catalog listing or a claim of
universal hardware support. Presence Pair for iPhone is still in TestFlight;
its public App Store release is pending Apple review. Initial enrollment needs
access to the iPhone app.

[![Validate](https://github.com/ravhello/presence-bridge/actions/workflows/validate.yml/badge.svg)](https://github.com/ravhello/presence-bridge/actions/workflows/validate.yml)
[![Release](https://img.shields.io/github/v/release/ravhello/presence-bridge?display_name=tag)](https://github.com/ravhello/presence-bridge/releases)

Presence Bridge adds an estimated iPhone presence and room to Home Assistant without a
cloud account and without installing permanent Home Assistant credentials on
the phone.

It consists of:

- a free Home Assistant custom integration;
- a Windows Bluetooth receiver for initial pairing;
- Windows observers and/or HA-native Bluetooth receivers for passive tracking;
- the Presence Pair iPhone app, used once to establish a private Bluetooth
  identity with an observer, with an optional foreground live-signal monitor.

After pairing, the receivers passively see rotating Bluetooth private
addresses. Home Assistant resolves them locally and exposes a presence binary
sensor, a room sensor, and a device tracker for each linked person. IRKs,
Bluetooth addresses, and MQTT credentials never leave the local network.

## Requirements

The iPhone does **not** need Wi-Fi for QR Bluetooth pairing. The Windows
receiver needs network access to HA and MQTT (Ethernet is fine). The optional
[live-signal monitor](docs/live-signal.md) is separate and needs HTTPS access
from the phone to HA. A Wi-Fi/network outage in that monitor does not remove
the Bluetooth identity. No pairing prompt must be accepted on the server.

- Home Assistant 2025.1 or newer with MQTT configured;
- a supported Windows installation with a Bluetooth LE adapter supporting
  central connections and peripheral advertising;
- Python 3.11 or newer on each Windows observer;
- Presence Pair on an iPhone running iOS 17 or newer.

During initial pairing, the configured Windows user must be signed in (a
locked session is sufficient). Passive observation runs as SYSTEM at startup.
Bluetooth strength estimates a receiver's room, not an exact position or a
guaranteed person count. Validate your own adapter and rooms before automating.

See the [compatibility matrix and release gates](docs/compatibility.md) before
choosing hardware. Native HA receivers receive advertisements only; they do not
replace the Windows enrollment receiver. Missing reception is an unknown
tracker state, not proof that the person left home.

## Install Home Assistant

### HACS custom repository

1. In HACS, open **Integrations**, then the three-dot menu and
   **Custom repositories**.
2. Add `https://github.com/ravhello/presence-bridge` as an Integration.
3. Enable pre-releases for this repository, select **v0.1.36**, install
   **Presence Bridge**, and restart Home Assistant.
4. Open **Settings > Devices & services > Add integration**, search for
   **Presence Bridge**, and complete setup.

HACS excludes pre-releases by default; see its
[pre-release switch documentation](https://www.hacs.xyz/docs/use/entities/switch/).
Use the exact version above rather than an older default release.

For manual installation, extract `presence_bridge-0.1.36.zip` into
`<HA config>/custom_components/presence_bridge/` and restart. The ZIP is flat:
`manifest.json` must be directly inside that directory, not another nested
folder. Back up HA before updating an existing installation.

## Install a Windows observer

Create a dedicated MQTT user first. Download the matching
`presence-bridge-windows-0.1.36.zip` from the release and extract it. On the
Windows computer, open PowerShell as Administrator in the extracted folder
containing `install.ps1` and run:

```powershell
Set-ExecutionPolicy -Scope Process Bypass
.\install.ps1
```

The installer asks for the observer name and MQTT credentials without placing
the password in shell history. It installs a SYSTEM startup task for passive
scanning plus an on-demand pairing task in the signed-in user's session. The
interactive task can write only its pairing data directory, not the code run
by SYSTEM. No UAC or Windows confirmation is needed for each QR scan.

Assign every fixed observer to its Home Assistant area in the Presence Bridge
panel. Room selection uses the observer with the strongest fresh signal.

## Pair an iPhone

1. Open **Presence Bridge** in the Home Assistant sidebar.
2. Select the person and the nearest Windows observer.
3. Tap **Create code**.
4. Open the code with Presence Pair or scan it in the app.
5. Keep the app open near the observer; accept Pair only if iOS asks. The receiver
   connects and accepts automatically.

The invitation is valid for ten minutes to scan. A scan completed in time starts
a single five-minute attempt on the iPhone, including retries and HA verification. Once the selected receiver verifies
that exact session, the invitation is consumed and cannot be reused. See
[Pairing protocol](docs/protocol.md) for the wire format and threat model.

## Privacy

Presence Bridge is local-only. The app does not contain Home Assistant or MQTT
credentials, does not use analytics, and does not transmit location data. See
[Privacy](docs/privacy.md).

## Documentation

- [English tutorial](docs/tutorial.md)
- [Windows observer](docs/windows-observer.md)
- [Pairing protocol](docs/protocol.md)
- [Privacy and security](docs/privacy.md)
- [Troubleshooting](docs/troubleshooting.md)
- [Italian tutorial](docs/tutorial-it.md)

## Status

The public protocol and integration are in preview. See the
[release notes](docs/release-0.1.36.md), [compatibility matrix](docs/compatibility.md),
and [changelog](CHANGELOG.md) for tested scope and remaining physical checks.
Presence Pair App Store availability is separate from this integration release.

## License

Presence Bridge is released under the MIT License. Presence Pair is a separate
proprietary application, currently free during compatibility testing, and is
not covered by this repository's license.
