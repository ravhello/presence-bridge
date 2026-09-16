---
title: Saved and partial Bluetooth pairing
permalink: /partial-pairing-compatibility/
---

# Saved and partial Bluetooth pairing

Normal enrollment must preserve valid saved bonds. A fresh reset is a diagnostic
scenario, not an enrollment prerequisite. Neither an OS `is_paired` flag nor a
strong RSSI proves a completed enrollment: require the current QR claim,
authenticated encryption, protected GATT acknowledgement, and HA identity commit.

## Product behavior

This matrix describes Windows recovery. Linux 0.2.0 reuses healthy bonds but
does not automatically delete an old bond on failure; see [Linux limits](linux.md).

| Initial condition | Expected behavior |
| --- | --- |
| Neither side remembers the bond | Normal numeric-comparison pairing and protected exchange. |
| Both retain a valid authenticated bond | Reuse it without asking to pair again; verify the current QR and protected exchange. |
| Only Windows retains the old bond | Reuse first. If the QR-verified peer rejects encryption, repair the exact phone once and pair again. |
| Only iOS retains the old bond | Windows requests a new authenticated bond. iOS may allow replacement or require the user to forget the obsolete peer. |
| Both retain an unusable bond | Try bounded reconnection first. After current QR proof and repeated failure, repair the old verified bond once. |
| Saved bond has insufficient security | After QR proof, replace it once rather than forcing the user to operate the server. |
| WinRT returns ALREADY_PAIRED during a race | Re-enumerate the exact device; accept reuse only if it is still paired with authenticated encryption. |
| Bond was created in this attempt | Reconnect without classifying it as an old bond or repeatedly creating new Pair prompts. |
| iOS rejects pairing, user cancels, proof changes | Stop without claiming success or deleting unrelated bonds. |
| GATT never opens and current QR proof cannot be read | Try one full uncached service lookup as well as filtered routes. Stop with actionable diagnostics, never erase an unverified peer based on its name or advertisement alone. |

The receiver cannot determine the contents of the iPhone bond database from an
advertisement. In particular, a failed GATT discovery alone does not distinguish
missing keys, iOS refusal, or a transport problem. No universal silent recovery
claim is justified. Core Bluetooth has no supported API for deleting a peer from
iOS Settings; user action may remain necessary in that case.

## Recovery invariants

- At most one automatic bond repair per pairing attempt, including address changes.
- Repair only an address whose current session and HMAC claim were verified.
- On Windows identify the BLE endpoint and its physical device container, then
  remove its classic and BLE endpoints together. Never match a friendly name.
- Preserve all other bonded devices and verify target removal before retrying.
- No adapter reset, shared radio restart, HA restart, or deadline extension.
- Keep newly created bonds distinct from old bonds reused later in the attempt.
- An unsuccessful repair stops; cancellation never becomes another pairing try.
- The test launcher that resets all state is not used for compatibility tests.

## Validation

`test_partial_pairing_recovery.py` exercises the product state transitions using
mock Windows and iOS outcomes, including old-bond rejection, persistent closure,
weak bonds, deadline, cancellation, and exact-container cleanup.
`test_numeric_pairing_probe.py` tests concurrent ALREADY_PAIRED with a fresh
authenticated state, weak state, and a no-longer-paired state.

These are deterministic software tests, not physical proof that every iPhone,
Windows driver, or Bluetooth adapter supports silent bond replacement. Test the
four physical initial-state combinations separately, preserving each setup.
After an explicit receiver-only cleanup, preserve the iPhone's remembered bond
to test that asymmetric case; do not reset both sides automatically.

Deployment on 2026-09-16: 161 receiver tests and 13 app-launch tests passed;
Ruff and diff checks passed. The two receiver modules were hash-verified on Dell
with backup `backup-before-partial-recovery-20260916-154552`. Only the observer
task was restarted, without clearing bonds or restarting Windows, HA or the radio.
HA reported RUNNING and live receiver observations advanced after deployment.
The private people-card cache is `people-devices-23-partial-bond-recovery`.
No physical compatibility-matrix attempt was performed during this deployment.

Apple's explanation of the OS-enforced lost-key condition:
https://developer.apple.com/forums/thread/132488 (Apple engineer response).
