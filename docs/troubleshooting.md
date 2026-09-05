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

## The receiver cannot find the app

- Start a fresh pairing session in the HA panel; the iPhone advertises only
  after Presence Pair scans that active code.
- Keep the phone within a few metres of the selected observer.
- Enable Bluetooth for Presence Pair in iOS Settings.
- Ensure another pairing session is not already using that observer.
- Follow the live step in HA. `waiting_for_iphone_advertisement` means the Dell
  is scanning but has not seen the app; `iphone_advertisement_seen` means radio
  discovery worked; `iphone_connected` means the GATT connection opened.

There is no prompt to accept on the Windows receiver. After scanning, leave
Presence Pair open. Only iOS may show an **Allow** or **Pair** prompt, and that
prompt must be accepted on the iPhone.

The app reports the stage that failed:

- `PP-BLE-*`: Bluetooth is off, unavailable, or not authorized on the iPhone.
- `PP-SCAN-01`: the Dell never saw the iPhone advertisement.
- `PP-CONNECT-01`: the Dell saw the iPhone but could not connect.
- `PP-VERIFY-01`: the receiver and QR session did not match.
- `PP-BOND-01`: the encrypted Bluetooth bond was not completed.
- `PP-SERVICE-01`: the Windows pairing service was incomplete.

Opening the HA panel or local QR helper in more than one tab does not create a
second invitation. Every tab receives the same active code until it expires;
only **New code** explicitly replaces it.

## iOS asks to pair but HA reports no identity

- Leave the app in the foreground until HA reports completion.
- Pair only one new phone at a time near the observer.
- Existing bonds are reused automatically. Remove one only if HA explicitly
  reports an ambiguous or unusable Windows identity.

## The room is wrong

- Assign every fixed observer to the correct HA area.
- Use at least two observers for room comparison.
- Avoid placing observers inside cabinets or directly behind televisions.
- RSSI is noisy; use the entities as evidence for an occupancy model rather
  than as a safety-critical position sensor.

## The phone becomes away briefly

Increase the **Away timeout** in the integration options. iOS system
advertisements are intermittent, especially while the phone is stationary.
