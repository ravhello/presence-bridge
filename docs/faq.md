---
title: iPhone Bluetooth presence in Home Assistant - FAQ
description: What Presence Bridge does, which Bluetooth receivers it needs, costs, privacy, Wi-Fi requirements and room detection limits.
permalink: /faq/
---

# Frequently asked questions

## What are Presence Bridge and Presence Pair?

Presence Bridge is a local Home Assistant integration for iPhone Bluetooth
presence and estimated rooms. Presence Pair is the companion iPhone app used
to enroll a phone through a temporary HA invitation, without manually copying
identity keys. After enrollment, receivers resolve rotating private Bluetooth
addresses locally and expose presence, room, RSSI and tracker entities.

## Is everything free and open source?

The HA integration and Windows/Linux receivers are free and MIT-licensed.
The separate iPhone app is proprietary and currently free during compatibility
testing. It may become paid in the future. The current release needs no purchase
or subscription. There is no developer cloud account.

## Can I install it from HACS?

Yes. Search for **Presence Bridge** in the default HACS catalog, install the
latest full release, restart HA and add the integration under Settings >
Devices & services. A custom repository and beta opt-in are not required.
[Open the HACS page](https://my.home-assistant.io/redirect/hacs_repository/?owner=ravhello&repository=presence-bridge&category=integration).

## Does installing the integration give HA control of any Bluetooth adapter?

No. Enrollment needs a compatible receiver with BLE central connections and
peripheral advertising. Windows uses a separate receiver and MQTT; its configured
user must remain signed in, although the screen can be locked. Linux/BlueZ
enrollment is experimental and needs D-Bus permissions and read-only bond
storage. Suitable Linux hosts can run it inside HA without MQTT.

HA OS, Green, restricted NAS and virtual machines generally need a separate
receiver. Supported HA Bluetooth proxies may extend passive reception, but
Presence Bridge does not implement first-time enrollment on ESPHome/Shelly/
Sonoff proxies. [Choose a setup](setup.md) before installing anything.

## Does the iPhone have to join Wi-Fi?

No for the Bluetooth exchange itself. Opening HA on the phone or using the
optional live-signal monitor needs network access to your HA. Remote receivers
need the local MQTT broker; in-process Linux enrollment uses HA directly.

## Must Presence Pair stay open all day?

The app must stay open near the receiver during initial enrollment. Subsequent
presence observations are passive and do not require a continuous app-to-server
Bluetooth connection. Phone advertisements and reception vary: missing samples
are not evidence that somebody left.

## Can this locate someone precisely, or measure metres?

No. It estimates the room of the **phone**, not necessarily its owner.
RSSI varies with walls, antenna position, body orientation and radio silence.
It is not precise ranging, wall-aware triangulation or proof of absence.
Never use these observations alone for locks, alarms or safety decisions.

## How is this different from Private BLE Device or other room-presence tools?

The main workflow here is owner-controlled iPhone enrollment, followed by
private-address resolution and HA entities. It is not a claim that other
presence projects are obsolete or incompatible. If an existing setup works,
keep it; start with the [architecture](architecture.md), [protocol](protocol.md)
and [compatibility matrix](compatibility.md) to judge whether this adds value.

## Does it replace my HA Person trackers?

No. You can add the generated Bluetooth tracker in Settings > People alongside
your existing GPS/Wi-Fi trackers. The integration reports that association but
does not edit HA's internal Person data. Missing BLE reception produces unknown,
not a definitive away decision.

## Which phones and receivers are verified?

Presence Pair requires iOS 17+. Windows has been used in the author's physical
setup; the full hardware matrix is not certified. Linux remains experimental.
The iPhone app does not implement Android or Apple Watch enrollment.
[See the evidence and limits](compatibility.md).

## How can I help or ask for support?

[Ask a question](https://github.com/ravhello/presence-bridge/discussions),
[report a bug or hardware result](https://github.com/ravhello/presence-bridge/issues/new/choose),
or improve the MIT integration and its documentation. Report the actual adapter,
OS and versions; do not post real QR invitations, identity keys, addresses,
credentials or HA backups. Read [privacy](privacy.md) and [security](https://github.com/ravhello/presence-bridge/security/policy).
