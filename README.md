# Presence Bridge

[![Validate](https://github.com/ravhello/presence-bridge/actions/workflows/validate.yml/badge.svg)](https://github.com/ravhello/presence-bridge/actions/workflows/validate.yml)

Free, MIT-licensed Home Assistant integration and Bluetooth receivers for local
iPhone presence and estimated room detection. No developer cloud or subscription.
The separate Presence Pair iOS app remains proprietary and is free during testing.

**0.2.2 is a public preview, not a universal hardware certification.** Linux is
experimental. Presence Pair is required for initial enrollment. Its public
[App Store listing](https://apps.apple.com/app/id6808059201) is pending Apple's
approval and may not open yet. As of October 21, 2026, build 220 is waiting for
review, not publicly downloadable. Do not install expecting to enroll an iPhone
unless you already have legitimate testing access to the app. The default HACS
submission remains on hold until the app is public; custom-repository access is
for existing testers, not a workaround for this requirement.

## Choose The Shortest Setup

| Installation | Enrollment receiver | MQTT |
| --- | --- | --- |
| HA Container on Linux with BLE, D-Bus and readable BlueZ bond storage | Inside the integration, experimental | Not required |
| Native Linux HA environment with the same permissions | Inside the integration, experimental | Not required |
| HA OS / Green / VM / restricted NAS | Separate Windows or Linux receiver on your LAN | Required |
| Debian/Raspberry Pi OS computer without HA | Headless Linux receiver, experimental | Required |
| Windows HA host or another Windows PC | Windows receiver | Required |
| ESPHome/Shelly/other HA Bluetooth proxy | Additional passive coverage only | Depends on proxy; not an enrollment receiver |

A BLE adapter must support central connections **and** peripheral advertising.
Linux also needs host BlueZ, system D-Bus permissions and read-only access to
its bond storage. Installing an HA custom component does not grant host/root
permissions, configure passthrough or install drivers.

[Installation chooser](docs/setup.md) · [Linux guide](docs/linux.md) ·
[Windows guide](docs/windows-observer.md) · [Compatibility](docs/compatibility.md)

## Install In Home Assistant

[Open HACS repository](https://my.home-assistant.io/redirect/hacs_repository/?owner=ravhello&repository=presence-bridge&category=integration)

1. Add `https://github.com/ravhello/presence-bridge` to HACS custom repositories,
   category **Integration**. It is not yet in the default HACS catalog.
2. Enable pre-releases, install **v0.2.2**, then restart HA once.
3. Add **Presence Bridge** under Settings > Devices & services.
4. Choose local Linux enrollment only if the host meets the requirements above.
   Otherwise configure HA's MQTT integration and install a remote receiver.
5. Open the **Presence Bridge** sidebar panel. It shows missing prerequisites,
   pairing-capable receivers, passive scanners and person associations.

Manual installation: extract `presence_bridge-0.2.2.zip` from
[Releases](https://github.com/ravhello/presence-bridge/releases/tag/v0.2.2) into
`<config>/custom_components/presence_bridge/`. The archive is flat; do not add
an extra directory layer. Back up HA before upgrading.

Existing Windows/MQTT installations keep their configuration. Local Linux
enrollment is opt-in for existing entries; it does not take over a radio on upgrade.

## Administrator Access

The panel, configuration WebSocket commands and all four services
(`start_pairing`, `cancel_pairing`, `set_observer_area`, `remove_identity`) are
administrator-only. Non-admin and unknown user contexts are rejected before
the coordinator is accessed, so they cannot create/read invitations or change
associations. HA's trusted internal automations without a user context remain
supported through the standard admin-service helper; do not expose these
actions through scripts or dashboards callable by untrusted users.

Users of earlier releases should upgrade to 0.2.2 and restart HA to apply this
permission fix. No pairing reset or receiver replacement is required.

## Pair And Use

Select an HA person and a nearby enrollment receiver, then create a code.
On the same iPhone, tap **Open Presence Pair**; on another screen, scan the QR.
Keep the app open near the receiver and accept the iOS Bluetooth request if shown.
No per-attempt server-side confirmation is required.

The QR allows ten minutes to start. The active attempt has a single five-minute
budget. Success requires the QR proof, authenticated Bluetooth bond, protected
acknowledgement, HA identity verification and saved association. A paired-device
entry alone is not success.

Wi-Fi is not needed for the Bluetooth exchange. Opening HA or the optional
live-signal monitor on the iPhone requires network access to HA. The receiver
needs access to HA in-process, or to the MQTT broker when remote.

After enrollment, passive observations update presence, room and device tracker
entities. Assign fixed receivers to real HA areas. RSSI is in dBm, **not metres**;
walls, orientation and radio silence limit precision. Missing reception is not
proof someone left home. Do not use this preview as an alarm, lock or life-safety
decision source.

## Public Collaboration

- [Contributing, local tests and receiver ports](CONTRIBUTING.md)
- [Hardware test matrix and known limits](docs/compatibility.md)
- [Architecture and extension contract](docs/architecture.md)
- [Protocol](docs/protocol.md), [privacy](docs/privacy.md), [security](SECURITY.md)
- [Report a bug or hardware result](https://github.com/ravhello/presence-bridge/issues/new/choose)
- [English tutorial](docs/tutorial.md), [Italian tutorial](docs/tutorial-it.md)
- [Release notes](docs/release-0.2.2.md), [changelog](CHANGELOG.md)
- [Physical acceptance checklist](docs/acceptance.md)

Only the integration, receivers, tests and documentation in this repository are
MIT licensed. The iPhone app source, signing material and commercial rights are
not included. This independent project is not affiliated with Apple, Nabu Casa,
Microsoft or the BlueZ project.
