---
title: 0.2.2 Administrator Service Security Preview
permalink: /release-0.2.2/
---

# 0.2.2 Administrator Service Security Preview

This update addresses the technical findings in the
[existing HACS review](https://github.com/hacs/default/pull/10597).

## Permission Fix

All four services now use Home Assistant's official administrator-service
helper: start pairing, cancel pairing, assign a receiver area and remove an
identity. Non-admin users cannot obtain invitations or alter associations
through these service calls. Unknown user contexts fail closed. Administrator
calls retain their response support, and trusted internal HA automation calls
follow the standard helper's behavior. Optional responses are returned only
when requested.

The panel and configuration WebSocket commands remain administrator-only.
Bluetooth remains a required integration dependency, with a regression check;
it was already required in the 0.2.1 source and release.
The manifest now declares the Presence & location category, resolving the
additional catalog-metadata warning from HACS validation.

## Upgrade

Back up HA, install the 0.2.2 integration ZIP (or enable pre-releases in HACS),
and restart HA once to replace the old service registrations. Do not clear
working bonds or remove the integration. Matching receiver packages are supplied
for version alignment; the change does not alter the BLE protocol or require
receiver re-enrollment. Verify `SHA256SUMS.txt` before using an archive.

Keep privileged scripts private. HA's internal no-user-context calls are trusted
by its standard helper; an untrusted user must not be able to trigger a script
that impersonates such an internal caller.

## App Availability And HACS

Presence Pair build 220 was resubmitted to Apple on October 18 and is still
waiting for review as checked October 21. Its intended public listing is
https://apps.apple.com/app/id6808059201, which may not be reachable before
approval. The integration cannot complete first enrollment without the app.
This release is for existing testers, not a claim the app is public.

The default HACS submission remains a draft. Do not re-request review until
the app is actually public and downloadable. Then verify the listing, update
the README and original HACS submission links and re-request review there;
do not create a duplicate submission.

## Verification Boundary

Regression tests cover registrations, real HA admin/non-admin/unknown/internal
service calls, Bluetooth dependency metadata and version alignment. HACS and
hassfest validate the packaged integration. These software checks do not certify
new radio hardware, Linux enrollment or universal iPhone compatibility. Version
0.2.2 remains an opt-in public preview, and the iOS source remains proprietary.
