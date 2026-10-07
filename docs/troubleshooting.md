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
- Follow the live step in HA. `waiting_for_iphone_advertisement` means the receiver
  is scanning but has not seen the app; `iphone_advertisement_seen` means radio
  discovery worked; `iphone_connected` means the GATT connection opened.

There is no prompt to accept on the Windows receiver. After scanning, leave
Presence Pair open. Only iOS may show an **Allow** or **Pair** prompt, and that
prompt must be accepted on the iPhone.

The app reports the stage that failed:

- `PP-BLE-*`: Bluetooth is off, unavailable, or not authorized on the iPhone.
- `PP-SCAN-01`: the receiver never saw the iPhone advertisement.
- `PP-CONNECT-01`: the receiver saw the iPhone but could not connect.
- `PP-VERIFY-01`: the receiver and QR session did not match.
- `PP-BOND-01`: the encrypted Bluetooth bond was not completed.
- `PP-SERVICE-01`: the Windows pairing service was incomplete.
- `PP-PROX-02`: the iPhone moved out of range after proximity had already
  been accepted. Presence Pair stops the attempt instead of continuing with a
  stale radio state; move the phone close again and choose **New code** in HA.

After the first proximity check, the Dell keeps the session-specific beacon
advertising while the iPhone discovers the GATT service, completes the
Bluetooth bond, and writes the protected acknowledgement. The app continues
to sample that beacon. Three consecutive readings at or below `-82 dBm`, or
ten seconds without a reading, fail the attempt with `PP-PROX-02`. Missing
samples produce a warning after three seconds. Once the protected exchange
finishes, missing beacons during HA persistence are no longer a range failure.
The threshold is a conservative radio guard, not a measurement in metres.

Opening the HA panel or local QR helper in more than one tab does not create a
second invitation. Every tab receives the same active code during its ten-minute
start window; only **New code** explicitly replaces it. Once the receiver verifies
the exact session, the QR is consumed. The attempt has one five-minute deadline;
neither retries nor the final verification restart it. A reused Windows bond may
not trigger an iOS Pair prompt. HA sends a session-specific Bluetooth completion
receipt after saving and verifying the identity, including on the existing-bond path.

## iOS asks to pair but HA reports no identity

- Leave the app in the foreground until HA reports completion.
- Pair only one new phone at a time near the observer.
- Existing working bonds are reused automatically. Discovery of a QR-scoped
  service alone does not authorize removal. If a saved bond blocks discovery,
  HA can offer an explicit, targeted **Repair saved pairing** action (see below).

### The receiver stopped pairing after accepting Pair

Check the last step in HA, not just the iPhone's saved Bluetooth devices:

- `numeric_comparison_verified`: Windows verified an authenticated bond. This
  does **not** mean the app exchange or HA enrollment has completed.
- `iphone_bond_reconnecting`: the encrypted confirmation channel closed. The
  receiver keeps the bond and opens a new connection, rereads the current QR
  session and HMAC proof, and retries the protected confirmation.
- `iphone_bond_link_failed`: those bounded recovery attempts failed. HA must
  not add an identity or report completion from the saved bond alone.

A disconnected channel does not itself mean Windows' service metadata is stale.
Recovery may reuse the freshly discovered service metadata, but session and claim
**values are read from the phone again**, and the protected write is mandatory.
Unreadable cached handles force an uncached lookup; a blocked filtered lookup
also gets a complete uncached service lookup before recovery fails. These routes
do not request another pairing ceremony or extend the five-minute deadline.
See [Bleak's Windows read semantics](https://bleak.readthedocs.io/en/latest/backends/windows.html).

After a terminal error, choose **New code** and reopen Presence Pair. Keep the
saved bond unless HA specifically requests removal. Do not restart HA or the
receiver during the attempt: restarting HA discards its temporary session keys
and requires a new invitation. No phone or receiver reset is needed for a normal
retry. A receiver-side fix does not require reinstalling the iPhone app.

### A saved phone never reaches QR verification

`iphone_saved_bond_unreachable` means Windows remembers a bond but the current
service could not be read after bounded retries. It is not by itself proof of a
key mismatch. The receiver preserves the bond and offers **Repair saved pairing**
only if the failed attempt's address resolves to exactly one stored identity.

1. Open the error in Home Assistant as an administrator.
2. Choose **Repair saved pairing** and confirm the targeted reset.
3. If the receiver is still in iPhone Settings > Bluetooth, forget it there too.
   iOS does not allow this app to delete a system bond automatically.
4. Use the new code that HA creates after verified receiver cleanup.

After verified removal, HA persists the phone-side preparation requirement for
that person and receiver until verified re-enrollment. QR renewal, cancellation
and HA restarts do not clear it. Presence Pair 219 and later opens a preparation
screen before starting Bluetooth: confirm **Removed or not listed: continue**
only after checking the selected receiver in iPhone Settings. If it is absent,
continue without deleting any other device. HA also displays these instructions
next to the QR so older apps do not hide the required manual step.

Ordinary retries do not carry this requirement and can reuse working bonds.
Temporary sessions and invitations are cleaned up independently; a timeout or
weak signal must not trigger indiscriminate system-bond deletion.

The repair uses the signed-in Windows Bluetooth broker, then SYSTEM verifies
both the OS bond and private keys are absent. A successful Windows API return
alone is not enough. Only the selected phone is removed; other phones, the HA
person and Wi-Fi assignments remain unchanged. A failure does not silently
clear the HA identity or start another QR. The repair offer expires after ten
minutes and cannot be used for another attempt or receiver. If no offer appears,
the target could not be identified safely; collect diagnostics instead of
deleting arbitrary Bluetooth devices. Working pairings never need this reset.

Controller error `0x3E` denotes a
connection-establishment or synchronization timeout, not an authentication
failure.

Each failed discovery must release its native `MaintainConnection` request
**before** cancelling the connection coroutine: Bleak's cancellation cleanup
discards the native session reference. Calling cleanup only afterwards is too
late to explicitly revoke the request. Timeout and user cancellation must both
drain the old operation before opening the next route. The admin-authorized bond
repair is separate from transport cleanup and never resets the radio.

References: [Windows connection lifetime](https://learn.microsoft.com/en-us/uwp/api/windows.devices.bluetooth.genericattributeprofile.gattsession.maintainconnection),
[Bluetooth controller error codes](https://www.bluetooth.com/wp-content/uploads/Files/Specification/HTML/Core-54/out/en/architecture%2C-mixing%2C-and-conventions/controller-error-codes.html).

## Removal fails or the server still remembers the phone

- Update both the integration and its Windows receiver to 0.1.26 or later.
- Keep the receiver that originally paired the phone online and its Bluetooth
  account signed in. A different nearby receiver cannot erase the original bond.
- Finish or cancel active pairing before removal. Removal never interrupts
  another phone's enrollment.
- HA keeps the identity when Windows has not confirmed full removal. Retry the
  same identity after resolving the error; partial removal targets are retained
  privately on Windows, even if the BLE key was already deleted.
- A successful result verifies both Windows endpoints and private-key absence.
  An entry still shown on iOS must be forgotten on the iPhone for a clean test.

## The room is wrong

- Assign every fixed observer to the correct HA area.
- Use at least two observers for room comparison.
- Avoid placing observers inside cabinets or directly behind televisions.
- RSSI is noisy; use the entities as evidence for an occupancy model rather
  than as a safety-critical position sensor.

## The phone becomes away briefly

Increase the **Away timeout** in the integration options. iOS system
advertisements are intermittent, especially while the phone is stationary.
