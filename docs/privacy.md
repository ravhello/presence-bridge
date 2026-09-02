---
title: Privacy
permalink: /privacy/
---

# Privacy and security

Presence Bridge is designed to keep presence inference inside the home.

## Data handled by the Home Assistant integration

- the selected Home Assistant `person` entity;
- a Bluetooth Identity Resolving Key (IRK);
- the closest configured observer, RSSI, and last-seen time.

The IRK is stored in Home Assistant's private `.storage` area. It is never
returned by the panel, entities, services, or diagnostics.

## Data handled by the Windows observer

- nearby BLE advertisements for a short rolling window;
- MQTT credentials stored in an ACL-protected configuration file;
- Windows Bluetooth bond keys managed by Windows itself.

Observation snapshots remain on the configured local MQTT broker. Logs contain
errors and counters, not IRKs or pairing secrets.

## Data handled by Presence Pair

- camera frames while the QR scanner is visible;
- a short-lived pairing invitation held in memory;
- the StoreKit purchase entitlement.

The app does not create an account, collect analytics, access contacts, request
location permission, or send data to a developer-operated server. Camera frames
are not stored. Pairing invitations are discarded when they expire or complete.

## Recommended deployment

- restrict the HA panel to administrators;
- use a unique MQTT account and limit it to the Presence Bridge topic tree;
- keep HA, MQTT, and observers on trusted LANs or encrypted links;
- keep Windows and iOS security updates installed;
- remove an identity in HA and the Windows Bluetooth bond when a phone changes
  owner.

## Disclosure

Security reports should be submitted privately through GitHub Security
Advisories for this repository. Do not include real IRKs, broker credentials, or
Home Assistant backups in a public issue.
