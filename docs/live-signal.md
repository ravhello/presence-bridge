# Live signal (0.1.36 candidate)

The HA identity snapshot includes `signal_receivers`, one fresh measurement per
matching receiver, with RSSI in dBm and the advertisement's `sampled_at` timestamp.
It is not the number of all powered-on scanners. Older rotated phone addresses
cannot pin RSSI to a historic maximum. Measurements expire after 45 seconds;
presence retention is separate. Refreshing a screen never refreshes a sample.

HA-native scanner intake runs every 5 seconds. External receivers retain their
configured publish interval (12 seconds on the test Dell). The home people card
fetches the bridge every 3 seconds and updates age labels every second without
replacing selectors, pending drafts or the pairing dialog. BLE advertisements
remain passive: iOS decides when to advertise, so continuous fresh readings
cannot be guaranteed when a phone is asleep or radio conditions are poor.

## Presence Pair access

An administrator chooses **Live signal in the app** for a paired identity. This
creates a separate, single-use signal QR, valid for ten minutes. It does not
unpair, pair, scan or connect Bluetooth. A compatible iOS build redeems this QR
over HTTPS and saves a read-only credential in the device-only Keychain.

The credential expires in 90 days and can be revoked from HA independently of
the Bluetooth association. Removing the identity revokes it too. Generating a
new QR preserves an existing app grant until the new QR is redeemed. Redeeming
replaces that identity's previous app grant. One app grant per identity is
currently supported. If redemption's response is lost, generate a new signal QR.

The endpoint exposes only that phone's label, current receiver names, areas,
RSSI and measurement times. No IRK, addresses, other people, history, HA bearer,
or home-control actions are accessible. Tokens are transmitted in a header and
only hashes are persisted by HA. QR contents must not be posted publicly.
Diagnostics omit receiver identities. Polls are no-store and rate-limited.

HTTPS uses normal iOS certificate verification, with an optional exact public
certificate fingerprint from the trusted admin QR for direct HA private TLS.
No global certificate bypass or HTTP fallback. Redirects are rejected. Native
HA HTTP storage supplies the certificate fingerprint on recent HA versions;
reverse proxies use their normally trusted certificate. Renewing a private
certificate may require a new signal QR.

The app polls only while foregrounded, handles unavailable/revoked access and
does not pretend that a network failure means Bluetooth was unpaired.

## Distance

RSSI is received power, not measured distance. `distance_m` is deliberately null
and `distance_status` is `not_calibrated`. A log-distance model could be added
after per-phone/per-receiver calibration at known distances, but walls, body
shielding and multipath prevent a claim of precise meters. Do not use this RSSI
view as a safety, lock or alarm authorization signal.

## Release gate

Build 215 was signed, passed four Xcode UI tests and reached TestFlight on
2026-09-12; installation was verified on 2026-09-13. A physical QR/Keychain/TLS
and network-loss test of the monitor remains necessary before public release.
Windows and Swift core tests do not replace it. Bluetooth commissioning is
unchanged, and unlike the monitor does not require Wi-Fi on the iPhone.

## Verification on 2026-09-12

- 176 Windows Python tests, 22 Swift core tests and the live people-card Node
  regression passed. SwiftUI files parsed; no Xcode build was run.
- HA full config validation: valid, no warnings/errors. HA returned RUNNING
  after restart and Presence Bridge loaded with the existing identity intact.
- Live scoped API: exact private TLS certificate pin matched, single-use
  redemption passed, four real snapshots received, revocation returned 401.
  Observed RSSI changed -74 / -74 / -85 / -74 dBm with new packet timestamps.
- Existing Chrome people page reloaded and visually verified: signal and sample
  age update without manually reloading the inventory. No new browser window.
- Test signal grant revoked at completion. No phone bond was removed and no
  pairing session was started. Native iOS Keychain/TLS/UI still need TestFlight.
