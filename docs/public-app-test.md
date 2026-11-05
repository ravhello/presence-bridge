---
title: Final Public App Test
permalink: /public-app-test/
---

# Final Public App Test

Use the normal HA panel and the public App Store app, not a diagnostic pairing
harness. This smoke test does not certify every adapter or the full
[acceptance matrix](acceptance.md). Record the real versions and timestamps.

For the current 0.2.4 test, verify that exact integration version is loaded
before certifying these changes. The App Store binary remains 1.0.0 (build 220);
no new phone build is required. Legacy private installations are not proof of
a 0.2.4 runtime pass and must not be overwritten without a backup/diff review.

## Operator Preparation

Verify the selected receiver's heartbeat, Bluetooth readiness and MQTT/HA
connectivity. Verify the admin panel loads and that no other attempt is active.
Keep the current association, other people's phones, radio and receiver service
intact. Record the baseline identity and current receiver version privately.
If an upgrade/restart is necessary, perform it outside the active attempt.
Prepare diagnostics that redact invitation secrets, IRKs and phone addresses.

Do not create a QR until the tester is ready: invitations have ten minutes to
start and the active attempt has one total five-minute budget. A disappeared
QR or interrupted connection is not proof of successful enrollment.

## iPhone Steps

1. Open [Presence Pair in the App Store](https://apps.apple.com/app/id6808059201)
   and install/open the public version, currently 1.0.0 (build 220), iOS 17+.
   If TestFlight was installed, use the App Store installation path for this
   test. Do not delete the app or its stored pairing data as routine preparation.
2. Turn Bluetooth on in Settings and allow Presence Pair's Bluetooth permission
   when requested. Stay near the selected physical receiver, not merely the HA
   VM or a passive proxy. About 20-30 cm is a useful controlled test condition,
   not a universal distance guarantee.
3. In HA choose your person and the correct enrollment receiver and create a
   new code. On that same iPhone tap Open Presence Pair; otherwise scan the QR
   displayed on another screen. Never scan an old saved QR or share it publicly.
4. Keep Presence Pair foregrounded, the phone near the receiver and the screen
   awake. Accept the iOS Pair prompt promptly if it appears. A valid saved bond
   may complete without another prompt; do not force removal to create one.
5. Wait for completion on both the phone and HA. If it fails, record the exact
   diagnostic code and time. Do not generate several overlapping attempts or
   clear bonds. The operator checks protected exchange, HA identity persistence
   and acknowledgement, not just the iOS Bluetooth device list.
6. After completion lock the screen and leave the phone near the receiver for
   up to two minutes. The operator checks for a genuinely newer passive BLE
   observation. An unchanged sample is inconclusive, not proof of absence.
7. Reopen HA and verify that the same person/phone is still associated without
   a duplicate. If the panel says Not assigned in People, open Settings > People,
   select the intended person, add the exact tracker shown and save without
   removing GPS/Wi-Fi trackers. Existing assignments need no change. Reopen the
   panel and confirm the message is gone; do not re-pair to change this setting.
8. Enable Live signal in the app for that same identity, using its separate
   read-only grant. Open it directly from HA on the same iPhone, or scan the
   monitor QR displayed on another screen. This is not another BLE pairing.
   Verify the signal's age/timestamp, not just a displayed RSSI number. The phone
   needs network access to HA (Wi-Fi or an existing secure VPN) for HA and this
   monitor; the Bluetooth pairing exchange itself does not require phone Wi-Fi.
9. Temporarily disconnect only the phone's network route to HA while keeping
   Bluetooth on. The monitor must report the network problem without claiming
   the phone was unpaired. Restore the route and verify it resumes. Do not
   publish new ports, disable certificate validation or replace the receiver.
   A self-signed TLS rejection requires a current admin-generated monitor code
   with the public-certificate pin; report the exact error rather than bypassing it.

## Separate Clean-Start Case

Only after a saved-bond test, and only with explicit tester agreement, remove
the exact phone through HA and wait for confirmed receiver-side removal. Then
forget that receiver in iPhone Settings > Bluetooth if listed. Record both
checks before creating a fresh QR. Do not reset the adapter or remove unrelated
devices. This destructive preparation is not required for ordinary retries.

## Result

Record pass/fail/not-tested for same-device launch or camera scan, protected
pairing and HA commit, identity persistence, fresh passive reception and any
optional live-signal test. Note actual elapsed time; five minutes is a timeout,
not a responsiveness target. Do not claim a clean-start, saved-bond, Linux or
second-phone pass unless that exact case was observed.
