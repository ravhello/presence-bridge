---
title: Troubleshooting
permalink: /troubleshooting/
---

# Troubleshooting

## No observer in Home Assistant

- Confirm MQTT is connected in HA.
- Check that the Windows task is `Running`.
- Read the end of `%ProgramData%\PresenceBridge\presence-bridge.log`.
- Confirm the observer and HA use the same broker and topic root.

## The app cannot find the bridge

- Start a fresh pairing session in the HA panel; advertising is intentionally
  disabled at all other times.
- Keep the phone within a few metres of the selected observer.
- Enable Bluetooth for Presence Pair in iOS Settings.
- Ensure another pairing session is not already using that observer.

## iOS asks to pair but HA reports no identity

- Leave the app in the foreground until HA reports completion.
- Pair only one new phone at a time near the observer.
- If the phone was previously paired with that Windows computer, remove the old
  Bluetooth bond in Windows and repeat with a fresh HA code.

## The room is wrong

- Assign every fixed observer to the correct HA area.
- Use at least two observers for room comparison.
- Avoid placing observers inside cabinets or directly behind televisions.
- RSSI is noisy; use the entities as evidence for an occupancy model rather
  than as a safety-critical position sensor.

## The phone becomes away briefly

Increase the **Away timeout** in the integration options. iOS system
advertisements are intermittent, especially while the phone is stationary.
