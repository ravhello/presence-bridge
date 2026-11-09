---
title: Share Presence Bridge - project links and media
description: Accurate English and Italian introductions, app screenshots and installation links for sharing Presence Bridge with Home Assistant communities.
permalink: /share/
---

# Share Presence Bridge

Use this page when introducing the project to a friend, local group, newsletter
or video audience. Adapt the text to the community and disclose your connection
to the project. Check its rules first, post once in the right place, and answer
questions rather than repeating links.

## Useful links

- [Project overview](https://ravhello.github.io/presence-bridge/)
- [Install with HACS](https://my.home-assistant.io/redirect/hacs_repository/?owner=ravhello&repository=presence-bridge&category=integration)
- [Presence Pair on the App Store](https://apps.apple.com/app/id6808059201)
- [English tutorial](tutorial.md) and [Italian tutorial](tutorial-it.md)
- [Compatibility and requirements](compatibility.md)
- [FAQ](faq.md), [privacy](privacy.md), [GitHub source](https://github.com/ravhello/presence-bridge)
- [Questions and announcements](https://github.com/ravhello/presence-bridge/discussions)

## Short introduction - English

Presence Bridge brings local iPhone Bluetooth presence and estimated rooms to
Home Assistant. Install the free MIT integration from HACS, enroll an iPhone
through a temporary invitation with Presence Pair, and use receiver observations
alongside your existing presence sensors. No developer cloud or subscription.
The proprietary iPhone app is currently free during compatibility testing.
Enrollment needs a compatible Windows receiver with MQTT, or experimental
Linux/BlueZ. Passive proxies alone cannot enroll an iPhone. RSSI is not exact
distance or proof that a person is absent.

## Presentazione breve - Italiano

Presence Bridge porta in Home Assistant la presenza Bluetooth locale degli
iPhone e una stima della stanza. L'integrazione e i ricevitori sono gratuiti e
MIT; si installa da HACS e si associa l'iPhone con un invito temporaneo e l'app
Presence Pair. Non servono cloud dello sviluppatore o abbonamenti. L'app iPhone
e proprietaria ed e attualmente gratuita durante i test di compatibilita; in
futuro potrebbe diventare a pagamento. Per il primo abbinamento serve un
ricevitore Windows compatibile con MQTT, oppure Linux/BlueZ sperimentale.
I proxy passivi estendono la ricezione ma non eseguono il primo abbinamento.
Il segnale aiuta a stimare la stanza del telefono, non misura metri precisi e
non dimostra da solo l'assenza di una persona.

## Screenshots and artwork

- [Presence Pair app start screen](assets/presence-pair-home.jpg)
- [Presence Pair app icon](assets/presence-pair-icon.png)
- [Clearly labeled offline demo](demo.md), with no real pairing credentials

These assets show the actual app UI, not somebody's home or private devices.
You may reproduce them unmodified to describe Presence Bridge/Presence Pair,
with attribution to Riccardo Ravello. They do not license the proprietary iOS
source or imply endorsement by Apple or Home Assistant. Do not use the Home
Assistant logo as this project's own branding.

## Keep these distinctions visible

- The integration and receivers are MIT; the separate app is proprietary.
- The app is free **now**, not a promise of permanent free pricing.
- Available in HACS does not mean official HA Core support or a security audit.
- Windows has physical setup evidence, not universal adapter certification.
- Linux and extended hardware support remain experimental.
- Estimates describe a phone, not continuous tracking of its owner.
- A pairing bond alone is not successful enrollment; the app and HA must finish.
- Development and documentation use AI assistance; disclose it when required
  and do not publish AI-generated posts in communities that prohibit them.

No paid placement, affiliate link, mailing list signup or fabricated review is
required to use or share the project.
