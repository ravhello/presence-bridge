---
title: Physical Acceptance
permalink: /acceptance/
---

# Physical Acceptance

Record exact app build, integration/receiver version, iPhone/iOS, adapter/driver
and installation mode. No passwords, QR URLs, identity keys or packet captures
belong in a public report. Software CI is not a hardware certification.

## Before Testing

- Install matching 0.2.1 integration/receiver packages and app build 219 or newer.
- Back up the protected configuration; do not disturb another person's phone.
- Verify the enrollment receiver is online and advertising, not just a passive proxy.
- Use a new invitation. Keep the app foregrounded and move near the selected radio.
- A valid existing bond may complete without an iOS Pair popup. That is expected.

## Required Cases

| Case | Expected result |
| --- | --- |
| Fresh, neither side paired | iOS consent if requested; protected ACK, HA association and phone completion |
| Valid bond on both sides | Reuse without unnecessary removal or repeated popup |
| Only iPhone remembers | Bounded recovery or actionable preparation; never false success |
| Only receiver remembers | Exact-target repair when available; no unrelated unpairing |
| Cancel, decline or timeout | Attempt stops within its budget; no partial success shown |
| Weak signal before connecting | Guidance to approach, not a claim that pairing completed |
| Move away during exchange | Clear interruption and bounded cleanup; new attempt remains usable |
| HA/MQTT interruption | No false completion; state recovers and retry remains possible |
| Explicit remove, then HA restart | Server removal verified; iPhone preparation survives restart |
| Second phone/person | No reassignment or deletion of the first phone |
| Passive tracking, screen locked | Fresh timestamps; stale reception never proves absence |
| Two receivers, away and return | Explainable estimates; no precise-distance promise |

For a clean-start test, explicitly remove only the selected phone in HA and wait
for verified server removal, then forget that receiver in iPhone Settings if it
is listed. This is a test preparation step, not a requirement for normal retries.

## Recording For App Review

Use the normal public HA panel and actual app, not a diagnostic harness or
simulated preview. Show app version, code creation (keep the recording private),
scan or same-device launch, proximity guidance, any iOS consent, successful
completion on the phone and HA, and a fresh presence/signal observation. The
video must show the exact binary selected for review. A failed attempt is useful
diagnostics, but is not evidence for a successful release gate.

## Report A Result

Use the repository's Hardware compatibility issue template for sanitized results.
State exactly which rows passed, failed or were not tested. Linux/ARM and other
adapter/iPhone combinations remain experimental until independently exercised.
