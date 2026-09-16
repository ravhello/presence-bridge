# 0.2.0 Public Preview

This release keeps the integration and receivers free and MIT licensed.
Presence Pair remains a separate proprietary iOS app, free during testing.

## Added

- Experimental Linux/BlueZ headless receiver, systemd and Docker deployment.
- Optional in-process Linux enrollment without MQTT.
- Prerequisite diagnostics and installation choices for containers, VMs and NAS.
- Local/remote routing, selected-peer authenticated key access, bounded pairing
  and scoped removal with original observation timestamps.
- Linux behavioral tests and an explicit platform validation matrix.

## Included Windows And UI Fixes

- Protected acknowledgement and partial-bond recovery improvements.
- Classic HA panel bootstrap, pre-upgrade hass assignment replay.
- Same-device app links, pairing guidance and setup prerequisite states.
- Public release packaging checks and receiver installation documentation.

## Upgrade

Back up HA and receiver state. Install matching integration and receiver
packages; restart HA once after changing Python code. Existing MQTT/Windows
entries do not enable Linux automatically. Do not install the Linux receiver
on your workstation when your actual Bluetooth host is a different server.

Linux is experimental and opt-in. No physical Linux/ARM/iPhone compatibility
claim is made from unit tests. Public App Store availability and Apple review
are separate from this integration release. See compatibility.md for remaining
hardware and launch gates.
