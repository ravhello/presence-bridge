---
title: 0.2.3 Public App Store Onboarding Preview
permalink: /release-0.2.3/
---

# 0.2.3 Public App Store Onboarding Preview

Presence Pair 1.0.0 (build 220) is Apple-approved. Its Italian
[App Store listing](https://apps.apple.com/app/id6808059201) was independently
checked on November 4, 2026: free, iOS 17 or later. TestFlight is no longer
required for first enrollment. Other storefronts were not independently checked.

## What Changes

This release updates documentation and version metadata only. It carries
forward the 0.2.2 administrator-only configuration services and required
Bluetooth dependency. It does not change the BLE protocol, erase associations,
repair every driver or certify additional hardware. Linux remains experimental.

The README, documentation homepage, installation guides, English/Italian
tutorials and same-iPhone app-link guide now point to the public app. The
[public-app test](public-app-test.md) separates app completion, HA persistence
and passive reception instead of treating an OS bond as sufficient proof.

## Install Or Upgrade

Default HACS inclusion is still subject to the existing
[submission](https://github.com/hacs/default/pull/10597); do not assume acceptance.
Until merged, add the repository as a HACS custom integration, enable
pre-releases and select v0.2.3, or use the matching release ZIP. Back up HA and
receiver configuration and verify SHA256SUMS.txt. The HA ZIP is flat: extract
directly into custom_components/presence_bridge, then restart HA once.

Existing 0.2.2 receivers use the same protocol. Their packaged 0.2.3 versions
only align metadata; a working receiver does not need an immediate reinstall
or fresh pairing for these documentation changes. Never reset a shared radio
or delete working phone bonds as routine upgrade preparation.

## Verification Boundary

Run the software validation and archive-content checks against the exact release
commit. App Store approval is not a substitute for the physical acceptance
matrix. Record fresh, saved-bond and partial-bond results separately. The
integration and receivers remain free/MIT; the iPhone source remains proprietary.
