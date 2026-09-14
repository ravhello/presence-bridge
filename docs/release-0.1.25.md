# Presence Bridge 0.1.25 release candidate

Companion integration and Windows receiver for the free compatibility edition
of Presence Pair 1.0.0, build 214. This is a preview, not a guarantee that every
iPhone, Windows driver, adapter or floor plan has been validated.

Publication hold, 2026-09-11: the original candidate archive does not contain
the encrypted-exchange gate and stale-bond recovery fixes. Do not publish it.
Rebuild the tracked-source archive and repeat the physical forget/re-pair test
before advancing this release, including a valid saved-bond reuse case.

## Changes since public 0.1.17

- A QR starts one five-minute attempt with proximity checks, retries and saved
  bond reuse. Missing iOS Pair prompts are not treated as errors by themselves.
- Success requires a successful encryption-protected acknowledgement on the
  iPhone, then a session-specific receipt after Home Assistant saves the
  identity. A saved Windows key is not enough to confirm pairing.
- Windows enrollment runs in a signed-in interactive session; passive presence
  runs as SYSTEM. The installer checks both central and peripheral BLE roles.
- Pairing exchange files are isolated from SYSTEM executable files. Updates
  refuse to interrupt an active enrollment; uninstall validates its target.
- Setup guides cover actual prerequisites, support reporting and limitations.

## Installation and update

Use the source archive of this exact release for both the HA integration and
`bridge/windows`. HACS uses the published GitHub release; a draft or tag alone
does not update users. This is a HACS custom repository, not yet a default HACS
catalog entry and not an official Home Assistant core integration.

1. Privately back up HA and the existing observer configuration/task settings.
2. Wait for any current pairing to finish. Keep existing iPhone/Windows bonds.
3. Update the integration through HACS and restart HA when convenient.
4. Run the new Windows installer elevated with the same observer ID and MQTT
   details. Record custom advanced settings first; setup writes its defaults.
5. Confirm observer availability and the existing linked phone in HA, then
   test a scan with Presence Pair 214. A new scan must not duplicate identity.

Do not deploy while recording review evidence. Do not restart Windows or clear
all Bluetooth bonds as a routine troubleshooting step.

## Validation and release gate

Run pytest, Ruff lint/format, PowerShell parser/ACL tests, hassfest and HACS
validation against the release commit. Archive only Git-tracked files and
record SHA-256 checksums. Keep this release draft until those gates pass.

The App Store video must show a real iPhone and receiver: QR scan, proximity,
any genuine iOS Pair request, and matching success in iPhone/HA. Reusing an
existing bond is allowed; explain that iOS may legitimately omit the popup.
Optionally show the private report preview without sending a fabricated bug.

## Rollback and limits

If existing identities disappear, the receiver stops publishing, or enrollment
regresses, stop new enrollments and restore the previous backed-up integration,
observer files/config and task settings as a matched set. Do not erase bonds
or HA identity storage to hide a regression. Older public 0.1.17 does not
implement the new completion receipt and is not a substitute for 0.1.24.

Room selection is based on fresh receiver signal strength. Walls, body
occlusion, sleep and driver behavior can change it; combine it with other home
sensors. Use no presence-only security or life-safety automation.

Reference requirements: [HACS publication](https://hacs.xyz/docs/publish/start/)
and [Apple submission](https://developer.apple.com/help/app-store-connect/manage-submissions-to-app-review/submit-an-app).
