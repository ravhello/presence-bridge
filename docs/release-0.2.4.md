---
title: 0.2.4 Supported HTTP API And Owner-Managed People
permalink: /release-0.2.4/
---

# 0.2.4 Supported HTTP API And Owner-Managed People

Presence Bridge is in the default HACS catalog: [submission #10597](https://github.com/hacs/default/pull/10597)
was approved and merged on November 5, 2026. Search for Presence Bridge and
install the latest full release; no custom repository or beta opt-in is needed.
The separate [Presence Pair app](https://apps.apple.com/app/id6808059201) remains
free during testing, iOS 17+, version 1.0.0 (build 220).

## HTTP Certificate

The optional live-signal QR uses the certificate path exposed by
hass.http.ssl_certificate, reads only the public certificate in an executor,
and never reads HA's .storage/http or private TLS key. If the public path is
unavailable or invalid, no pin is added and normal iOS certificate validation
remains required. No insecure transport or certificate bypass is introduced.
Existing monitor grants remain valid; a new code may be needed after certificate
renewal as before.

## Person Assignment

Initial BLE pairing still saves the selected phone/person association inside
Presence Bridge. To also include the generated tracker in an HA Person's
aggregate state, the owner selects it in Settings > People, adds it alongside
existing GPS/Wi-Fi trackers and saves. YAML-defined people are managed in YAML.

The integration no longer modifies hass.data["person"] or its internal storage
collection. Existing links are preserved, not migrated or removed. Assignment
status is read from public entity state on every panel refresh, not from an old
cached success flag. Renamed tracker entity IDs are read from HA's entity
registry; another person's assignment is flagged and never silently changed.
The panel shows the exact tracker and a Settings > People link when action is
needed. This optional aggregation step is not a failed Bluetooth pairing.

## Upgrade And Test

Back up HA and receiver configuration. Update the integration through HACS and
restart HA outside an active pairing attempt; refresh the panel afterwards.
Do not remove phones, reset a radio or replace a private patched installation
blindly. Public 0.2.3 receivers use the same BLE protocol; a receiver reinstall
or new iPhone build is not required for these two improvements. The matched
0.2.4 receiver packages only align their version metadata.

Use the [final public-app test](public-app-test.md) to check an existing bond,
HA identity persistence, owner-managed Person assignment and live HTTPS signal.
The physical iPhone/UI/TLS outcome is separate from passing automated tests.
Linux remains experimental and HACS inclusion does not certify all hardware.
