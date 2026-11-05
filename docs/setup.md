---
title: Choose Your Installation
permalink: /setup/
---

# Choose Your Installation

Install Presence Bridge from the default HACS catalog (accepted November 5, 2026).
Search its name and choose the latest full release; no custom repository or
beta switch is needed. Existing custom-repository installations can be kept.

## Local Linux: Fewest Components, Experimental

Use the integration's **Local Linux receiver** option when Home Assistant can
reach a powered BLE central/peripheral adapter through host BlueZ and system
D-Bus, and can read the selected adapter's bond storage.
No MQTT broker, separate process or Windows computer is then required.

For HA Container, configure the host and mounts in [Linux setup](linux.md).
For HA OS, do not assume the Core container can read the host Bluetooth keys.
If the panel reports missing bond storage, use a remote receiver instead.
There is no tested HA OS add-on in this release and no automatic privilege bypass.

Integration options include a stable adapter MAC. Leaving it empty works only
when exactly one local adapter exists; ambiguity stops setup. The integration
never switches radios silently, turns one on, resets USB or changes the default
system Bluetooth agent. Reload the integration after fixing host prerequisites.

## Remote Receiver: HA Anywhere On The LAN

Configure HA's MQTT integration and a dedicated MQTT account, then use:
- [Windows](windows-observer.md): startup scanner and on-demand pairing helper.
  Initial enrollment requires its configured user to be signed in; a locked
  desktop works, the login screen after reboot does not.
- [Linux](linux.md): headless systemd service or Docker container, experimental.
  No desktop session is required.

HA can run on HA OS, a VM, NAS or another LAN computer. The Bluetooth radio
belongs to the receiver; it does not need to be passed through to HA. Never
assign the same USB radio exclusively to both host and guest.

MQTT carries private observations and temporary QR secrets. Use a dedicated
broker account and topic ACLs. TLS is required across untrusted segments.
Do not expose MQTT, BlueZ D-Bus or Bluetooth key directories to the internet.

## iPhone

Presence Pair needs iOS 17+, Bluetooth permission and proximity. Camera
permission is needed only for scanning a QR. An HA link on the same iPhone
opens the app directly. The Bluetooth exchange does not need phone Wi-Fi.
The optional signal monitor and access to HA do need network connectivity.

Install [Presence Pair from the App Store](https://apps.apple.com/app/id6808059201).
The public app is free during testing; no TestFlight invitation or purchase is
required. Apple approval and the Italian public listing were verified on
November 4, 2026. This does not certify an untested receiver or installation mode.

## Extra Coverage

Existing HA Bluetooth scanners/proxies are used for passive observation.
They cannot be assumed to advertise our proximity/receipt services, create a
bond or export an identity key. A Zigbee radio is not automatically a BLE
enrollment receiver. Pair once, then test passive reception in each real room.

The receiver that owns the original bond remains responsible for server-side
removal. Keep it available for identity management.

## Upgrade, Removal And Recovery

Back up HA and receiver configuration before upgrades. Install matching
integration and receiver versions; refresh browser caches after frontend changes.
Existing person, Wi-Fi/GPS and room assignments are preserved.
Starting with 0.2.4, new trackers are added to an aggregate HA Person by the owner
in Settings > People, not through HA's internal collection. The panel exposes
the exact tracker ID and checks the current assignment on refresh. A successful
BLE pairing does not require completing this optional aggregation setting.

Remove a phone through Presence Bridge: the owning receiver must confirm its
specific OS bond is gone before HA forgets the association. iOS may still
require **Forget This Device** on the phone; neither HA nor an app can silently
edit iOS system bonds. Cancel an active pairing before changing receivers.

Removing the HA integration alone does not erase OS Bluetooth bonds. Remove
phones first if a complete teardown is intended. Backups contain sensitive
identity material; protect or delete them according to your own retention policy.
