# Changelog

All notable changes to Presence Bridge are documented here.

## 0.1.2 - 2026-09-02

- Applied the ten-minute invitation lifetime limit to the packaged Windows
  observer protocol as well as the Home Assistant integration.

## 0.1.1 - 2026-09-02

- Enforced the documented ten-minute maximum pairing lifetime in the protocol
  parser, matching the existing Home Assistant service schema.
- Added a complete English setup tutorial alongside the Italian guide.

## 0.1.0 - 2026-09-02

- Added the local-push Home Assistant integration and administrator panel.
- Added private BLE identity resolution with presence, room, and tracker entities.
- Added the Windows observer with boot recovery and MQTT compatibility output.
- Added app-assisted encrypted GATT pairing for Presence Pair on iPhone.
- Added redacted diagnostics, English and Italian translations, and public docs.
- Added installer checks for both the Bluetooth adapter and the running SYSTEM task.

This is the first public preview. Use Bluetooth room estimates as one input to
an occupancy model, not as a safety-critical signal.
