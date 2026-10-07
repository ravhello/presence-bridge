---
title: 0.2.1 Recovery Preview
permalink: /release-0.2.1/
---

# 0.2.1 Recovery Preview

Presence Bridge and its receivers remain free and MIT licensed. The proprietary
iPhone app remains free during compatibility testing; App Store approval is separate.

## Changes

- Preserve valid bonds during normal retry. Discovery timeout alone never proves
  invalid keys and never grants permission to delete a saved bond.
- Offer explicit administrator repair only for the exact failed Windows session
  and uniquely identified phone. Offers expire and cannot affect a different phone.
- Confirm receiver removal before clearing HA. Persist preparation for the same
  person and receiver across cancellation, QR renewal and HA restart.
- With Presence Pair 219 or newer, show the iPhone Settings preparation screen
  before starting Bluetooth after verified server removal. iOS bond deletion is
  manual; neither the app nor HA can silently remove it.
- Keep proximity advertising active while pairing, release connection ownership
  before cancellation, and refresh services after authenticated bonding.
- Continue requiring the protected acknowledgement, HA identity commit and phone
  receipt. A system paired-device entry is not enrollment success.

## Install Or Upgrade

Back up HA and receiver state. Use matching 0.2.1 integration and receiver ZIPs;
verify `SHA256SUMS.txt`. In HACS enable pre-releases. Restart HA after replacing
integration Python files; update the receiver on the actual Bluetooth server.
Do not delete working bonds or remove the integration just to upgrade.

Windows enrollment needs its configured user signed in (locked is fine), an
adapter with BLE central and peripheral roles and MQTT. Linux remains opt-in and
experimental, with BlueZ, D-Bus and read-only bond storage requirements. Passive
proxies cannot perform first enrollment. See [installation](setup.md).

## Verification Boundary

Automated tests exercise protected exchange, scoped removal, persistence,
timeouts, cancellation, app links and prerequisites. Archive validation checks
required files and source hashes. CI also exercises Linux-only storage cases.
These checks do not replace RF tests or certify every driver/iPhone combination.

The final 0.2.1 / iOS 219 physical run and review video are still required. Use
the [acceptance checklist](acceptance.md). This is an opt-in preview, not a claim
of universal compatibility or a stable App Store launch.

## Rollback

Stop enrollment before rollback. Preserve a backup of HA's protected integration
storage and receiver configuration. Restore the matching previously working
integration and receiver packages, then restart their services. Do not publish
backups, keys, QR invitations or radio captures in an issue.
