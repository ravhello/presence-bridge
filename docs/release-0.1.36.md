---
title: Presence Bridge 0.1.36 public preview
permalink: /release-0.1.36/
---

# Presence Bridge 0.1.36: public preview

Released on 2026-09-14 as a free, opt-in pre-release. The Home Assistant
integration and Windows receiver are public; the iPhone app remains in
TestFlight while its separate App Store submission is prepared.

## Included

- QR-scoped iPhone pairing on a headless Windows receiver, protected Bluetooth
  acknowledgement and verified HA identity storage before success.
- Bounded saved-bond recovery and removal of the selected phone on its owning
  receiver, without clearing unrelated devices.
- Passive Windows and HA-native Bluetooth observations, fresh room estimates
  and Person linking that preserves existing GPS/Wi-Fi trackers.
- Live per-receiver dBm readings, packet timestamps and expiration of stale
  measurements. RSSI is not a precise distance measurement in meters.
- A separate, revocable, read-only HTTPS signal QR for the iPhone monitor.

## Install

1. Add `https://github.com/ravhello/presence-bridge` to HACS as a custom
   Integration repository. This release is not a default HACS catalog listing.
2. Enable pre-releases for this repository and select **v0.1.36**. HACS excludes
   betas by default; see its
   [pre-release switch documentation](https://www.hacs.xyz/docs/use/entities/switch/).
3. Install, restart HA, and add Presence Bridge from Devices & services.
4. Extract `presence-bridge-windows-0.1.36.zip` on the fixed Windows receiver
   and run `install.ps1` as Administrator from the extracted directory.

For manual HA installation, extract `presence_bridge-0.1.36.zip` directly into
`<HA config>/custom_components/presence_bridge/` and restart. The ZIP is flat;
`manifest.json` belongs directly inside that directory.

[Download both packages](https://github.com/ravhello/presence-bridge/releases/tag/v0.1.36).
Full tutorials:
[English](https://ravhello.github.io/presence-bridge/tutorial/) and
[Italian](https://ravhello.github.io/presence-bridge/tutorial-it/).

## Compatibility limits

Initial enrollment requires Presence Pair on iOS 17+ and a Windows Bluetooth
LE adapter with central and peripheral support. The configured Windows user
must be signed in; a locked session is sufficient. No PIN prompt needs to be
accepted on the receiver. Windows passive tracking runs as SYSTEM at startup.

The iPhone does not need Wi-Fi for Bluetooth pairing. The receiver needs a
network connection to HA/MQTT. The optional HTTPS live monitor separately needs
a network route from the iPhone to HA.

HA-native Bluetooth adapters and compatible proxies provide passive tracking,
not initial enrollment. Linux/BlueZ, ESPHome/Shelly/Sonoff initial enrollment,
Android enrollment and Apple Watch enrollment are not implemented.

The final public-flow demonstration, a clean install on another Windows host,
the new iOS monitor's physical tests and the wider phone/adapter matrix remain
pending. A saved working identity and unit tests do not certify those cases.
Read the [compatibility matrix](https://github.com/ravhello/presence-bridge/blob/main/docs/compatibility.md).

## Verification

- 180 Python tests, Ruff lint/formatting, Windows installer ACL checks, HA
  hassfest and all 9 HACS checks passed in
  [CI](https://github.com/ravhello/presence-bridge/actions/runs/34857118964).
- Packaged code is identical to tested source commit
  `89cf2714045dc35e8402441eca6cc0efda6d8635`. Publication changes only documentation.
- All 23 integration files and 13 receiver files match source hashes; private
  configuration, credentials and diagnostic launchers are excluded.
- Existing HA 2026.8.3 deployment was checked read-only with a linked identity
  and fresh observations. Publication does not reset or restart that deployment.

SHA-256:

```text
426f2afa6619ec2de970719d3a160ed646a90359be3408b36e49d076379a964a  presence_bridge-0.1.36.zip
e8dc8f4118af1a32c093d63a71d2e3cc5bd4e977cbf0b37d1633e8b2ecb6adcb  presence-bridge-windows-0.1.36.zip
```

## Upgrade and recovery

Before updating, create an encrypted HA backup and securely back up the receiver
configuration. Keep the receiver ID unchanged. Update HA and Windows to the
matching version without dissociating a working phone. Do not open pairing
while installing or restarting either component.

If an update fails, pause only presence-dependent automations and use a
coordinated restoration of the previous HA backup and receiver version/config.
Do not delete identities or clear the Bluetooth adapter as a generic fix.
Older software is not guaranteed to understand newer stored data.

## Report problems

Use [GitHub issues](https://github.com/ravhello/presence-bridge/issues/new/choose)
for the integration or **Report a problem** inside Presence Pair. Include HA,
Windows, adapter/driver, iOS, app build and receiver versions, the diagnostic
code, expected behavior and reproduction steps. Review diagnostics before
sending; do not attach QR codes, identity keys, credentials or HA backups.
Report security issues privately through the repository's Security tab.
