---
title: Presence Bridge - iPhone Bluetooth presence for Home Assistant
description: Local iPhone presence, room estimates and live Bluetooth signal in Home Assistant. Free MIT integration in HACS, with Windows and experimental Linux receivers.
permalink: /
---

# Presence Bridge

Local room-level iPhone presence for Home Assistant, with no developer cloud
and no permanent HA credential on the phone.

[Install with HACS](https://my.home-assistant.io/redirect/hacs_repository/?owner=ravhello&repository=presence-bridge&category=integration)
| [Get the iPhone app](https://apps.apple.com/app/id6808059201)
| [English setup](tutorial.md) | [Guida italiana](tutorial-it.md)

## What it helps with

Enroll an iPhone without manually extracting its Bluetooth identity key, then
use local observations as one signal for presence and estimated rooms. Combine
these entities with motion sensors, GPS/Wi-Fi and your existing HA logic for
comfort automations. You decide what the observations control.

The **integration and receivers are free and MIT-licensed**. The separate iOS
app is proprietary and **currently free during compatibility testing**; it may
become paid in the future. There is no developer cloud or subscription.

<img src="{{ site.baseurl }}/assets/presence-pair-home.jpg" alt="Presence Pair iPhone start screen with the Scan code button" width="230" height="500" style="max-width:100%;height:auto;" />

*Presence Pair app start screen. Initial enrollment needs a compatible receiver;
a passive Bluetooth proxy alone is not enough. RSSI provides room estimates,
not exact distances. Linux support is experimental.*

[Check requirements first](setup.md) | [Frequently asked questions](faq.md)

## Release 0.2.4

The free integration and Windows/Linux receivers are available in the
[0.2.4 full release](https://github.com/ravhello/presence-bridge/releases/tag/v0.2.4).
Search for Presence Bridge in the default HACS catalog; neither a custom
repository nor beta opt-in is required. Manual ZIP installation is also available.
Read the [release notes]({{ site.baseurl }}/release-0.2.4/)
for installation, upgrade and compatibility limits.

Install [Presence Pair from the App Store](https://apps.apple.com/app/id6808059201)
for initial enrollment. The app is free during testing and requires iOS 17+;
TestFlight is not required. Version 1.0.0 (build 220) is Apple-approved and the
Italian public listing was verified on November 4, 2026. HACS inclusion was
approved and merged on November 5. Linux and the wider hardware matrix remain experimental.

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

Assign each generated Bluetooth tracker in HA **Settings > People** if it should
contribute to that Person's aggregate state. Presence Bridge reports the current
assignment without modifying HA's internal person data. Existing links are preserved.

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
- [Ask the community](https://github.com/ravhello/presence-bridge/discussions)
- [Introduce the project to others]({{ site.baseurl }}/share/)

Presence Bridge is an independent project and is not affiliated with Nabu Casa,
Apple, or Microsoft.
