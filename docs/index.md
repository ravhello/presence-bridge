---
title: Presence Bridge
permalink: /
---

# Presence Bridge

Local room-level iPhone presence for Home Assistant, with no developer cloud
and no permanent HA credential on the phone.

[Install from GitHub](https://github.com/ravhello/presence-bridge){: .btn }

## Public preview 0.2.3

The free integration and Windows/Linux receivers are available in the
[0.2.3 pre-release](https://github.com/ravhello/presence-bridge/releases/tag/v0.2.3).
Use the HACS custom repository with pre-releases enabled, or install the ZIP
manually. Read the [release notes]({{ site.baseurl }}/release-0.2.3/)
for installation, upgrade and compatibility limits.

Install [Presence Pair from the App Store](https://apps.apple.com/app/id6808059201)
for initial enrollment. The app is free during testing and requires iOS 17+;
TestFlight is not required. Version 1.0.0 (build 220) is Apple-approved and the
Italian public listing was verified on November 4, 2026. Default HACS inclusion
is still under review. Linux and the wider hardware test matrix remain experimental.

## Components

Local Linux enrollment can run inside HA without MQTT when BlueZ, D-Bus,
BLE advertising and read-only bond storage are accessible. Otherwise use a
separate Windows or Linux receiver with MQTT. Linux is experimental. HA may run on
a Raspberry Pi, HA OS, a VM or a Container elsewhere on the same LAN. The
integration uses configured HA Bluetooth scanners but does not install drivers
or configure the host radio. [Choose your installation](setup.md).

**Presence Bridge for Home Assistant** is free and open source. It resolves
private Bluetooth addresses locally and creates presence, room, and tracker
entities.

**Windows observer** runs on an always-on computer with Bluetooth LE and sends
fresh, bounded observations to the user's own MQTT broker.

**Presence Pair for iPhone** performs the one-time encrypted association. The
current compatibility release is free, with no required purchase or
subscription. It includes optional, previewable support reports and is now
available from the App Store.

**Linux receiver (experimental)** supports headless BlueZ enrollment, in-process
inside suitably configured HA or remotely over MQTT. [Linux setup](linux.md).

## Start here

- [English tutorial]({{ site.baseurl }}/tutorial/)
- [Italian tutorial]({{ site.baseurl }}/tutorial-it/)
- [Offline demo QR (app build 220+)]({{ site.baseurl }}/demo/)
- [Windows observer]({{ site.baseurl }}/windows-observer/)
- [Troubleshooting]({{ site.baseurl }}/troubleshooting/)
- [Privacy]({{ site.baseurl }}/privacy/)
- [Protocol and threat model]({{ site.baseurl }}/protocol/)
- [Physical acceptance checklist]({{ site.baseurl }}/acceptance/)
- [Final public-app test]({{ site.baseurl }}/public-app-test/)

Presence Bridge is an independent project and is not affiliated with Nabu Casa,
Apple, or Microsoft.
