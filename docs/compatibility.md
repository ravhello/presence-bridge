# Compatibility and release verification

## Two different capabilities

Pairing creates a private Bluetooth identity using a short-lived QR invitation.
Presence tracking later resolves advertisements locally without reconnecting to
the phone. A receiver that hears advertisements cannot necessarily create bonds
or export identity keys.

| Component | Implemented support | Verification status |
| --- | --- | --- |
| Initial app pairing | Windows receiver, BLE central and peripheral roles; iOS 17+ Presence Pair | One physical iPhone/Dell setup completed authenticated GATT and HA storage on receiver 0.1.34; standard 0.1.35 path still needs a physical run |
| Windows passive tracking | MQTT Windows observer | Live deployment verified; coverage depends on antenna and phone advertisements |
| Native HA Bluetooth tracking | Shared HA Bluetooth cache, including non-connectable scanners and supported proxies | Native API and timestamp behavior covered by local tests; per-installation runtime verification required |
| HA host | HA OS, Container or other supported HA installation with MQTT | HA 2026.8.3 used for deployment; not every older HA release has been tested |
| Linux/BlueZ initial pairing | Experimental in-process or remote receiver, BlueZ key storage read-only | Behavioral tests only; physical Linux/iPhone and ARM certification pending |
| ESPHome/Shelly/Sonoff initial pairing | Not implemented by Presence Bridge | Passive HA-compatible radio support does not imply enrollment support |
| Android/Apple Watch enrollment via app | Not implemented by the iPhone app | Do not describe as universally compatible |

The Windows receiver's configured user must remain signed in for initial
pairing; the desktop may be locked and no server-side PIN dialog is required.
The passive Windows observer runs as a service task. No desktop/workstation
outside the configured receiver is required by the public pairing flow.

## Presence semantics

- `binary_sensor` on means a recent matching advertisement, not proof that its
  owner is carrying the phone.
- `device_tracker` reports home when detected and unknown when not detected.
  Missing radio coverage must not establish a person's absence by itself.
- All receivers offline makes the identity entities unavailable.
- A room estimate requires a sample no older than 45 seconds. A 6 dB margin
  reduces switching between comparable receivers; old strong samples do not
  override a newer receiver. RSSI does not provide exact distance or wall-aware
  triangulation.
- Native HA cache intake uses HA's selected source for an address. It does not
  expose simultaneous RSSI from every adapter or claim triangulation.
- Existing GPS/Wi-Fi trackers are preserved when adding a paired identity to
  its selected editable HA Person. YAML-defined people must be updated in YAML.
- Private keys stay in HA's protected configuration and the receiver bond
  store, never in public entity attributes or support diagnostics. Treat HA
  backups as sensitive and use encryption when exporting them.

## Release gates

Local unit tests cannot certify every radio/driver combination. Before a stable
release, verify the normal public HA panel and installer on a clean supported
Windows machine, then test fresh pairing, a valid saved bond, one-sided stale
bonds, cancellation, timeout, user decline, HA/MQTT restarts, removal and a
second phone. Confirm protected GATT ACK, HA persistence and phone receipt for
each successful enrollment. No test may unpair unrelated devices.

The current development recovery is bounded to one repair per attempt, even
if the peer's address rotates. It requires the current verified QR claim and
an old bond: weak protection, protected-write rejection or bounded persistent
post-verification connection failures can trigger recovery. Pre-claim radio
timeouts never authorize unpairing. See the [state matrix](partial-pairing-compatibility.md).
These recovery changes are included in the 0.2.0 preview; older 0.1.36 archives do not contain them.

The [installation matrix](setup.md) distinguishes HA hosting from enrollment.
The integration does not install a Windows receiver or manage the host's radio
driver. MQTT is required for remote receivers, but not for local Linux enrollment.
The local mode requires system D-Bus and readable BlueZ bond storage; HA OS
does not automatically expose that storage to Core. Linux is headless and does
not require a logged-in graphical user. See [Linux setup](linux.md).

Exercise screen lock/app background, away/return and at least two independent
receivers before using room estimates for consequential automations. Keep the
current working pairing when running software-only tests. The private physical
clean-room harness deliberately removes only the selected phone before a fresh
attempt; this is not a policy to reset working bonds on every normal QR scan.

Publish a beta with these explicit limits before claiming broader hardware
support. Stable App Store submission still requires the final tested binary,
hardware demonstration video and Apple review; none of these are replaced by
successful unit tests.

References: [HA Bluetooth APIs](https://developers.home-assistant.io/docs/core/bluetooth/api/),
[Private BLE Device](https://www.home-assistant.io/integrations/private_ble_device/).
