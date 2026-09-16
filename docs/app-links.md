# Open Presence Pair from the same iPhone

The pairing and live-signal panels pass the existing `presencepair://` invitation
directly to the iOS app when opened on an iPhone or iPad. The native `onOpenURL`
handler already routes it through the same validated invitation parser and
Bluetooth workflow used by the QR scanner. No camera interaction or new app
build is required for this entry point.

Desktop and Android browsers show an App Store download link instead. The store
URL is fixed and never contains an invitation, identity key, or session secret.
Expired, consumed, cancelled, and completed pairing invitations cannot be opened.

## Home Assistant Companion and fallback limits

The button uses `window.open(uri, "_blank", "noopener,noreferrer")` synchronously
inside the tap, matching HA's URL-action path. The link itself also has
`target="_blank"`. Companion's `WKUIDelegate.createWebViewWith` routes a new-window
request to `openURLInBrowser`, which hands non-HTTP URLs directly to iOS. This
does not navigate or replace HA's own webview.

The initial same-window `location.assign` implementation was incorrect for the
Companion webview: a custom scheme was treated as page loading, and could leave
HA showing a connection error instead of launching the app. It is removed.

There is no automatic timed Store redirect. Browsers cannot reliably query
whether an iOS app is installed, and `window.open` returning `null` is normal
with `noopener` and with Companion's native handoff. Treating that as failure
can send an already-installed TestFlight user to the Store. The explicit App
Store link remains available; fully automatic absent-app fallback requires
Universal Links and a new signed app build, not a timer.

Official source references:

- [HA URL actions](https://github.com/home-assistant/frontend/blob/dev/src/panels/lovelace/common/handle-action.ts)
- [Companion WebKit delegates](https://github.com/home-assistant/iOS/blob/main/Sources/App/Frontend/WebView/WebViewController/WebViewController%2BWebKitDelegates.swift)
- [Companion external URL opener](https://github.com/home-assistant/iOS/blob/main/Sources/App/Utilities/Utils.swift)

An iOS confirmation to open Presence Pair, or a Bluetooth system pairing prompt,
may still be required. Those are not QR scans and are not bypassed.

The App Store link uses app ID `6808059201`. As checked on 2026-09-16, the Italian
public catalog does not list it yet. TestFlight remains the installation path
until Apple approves and releases the app. Installing the app does not resume a
consumed or expired invitation: return to HA and open a current code.

Universal Links are a possible follow-up requiring an associated public domain,
an AASA file, a new signed app build, and device validation. This patch does not
claim to provide that OS-level installed-app routing.

## Verification

Run locally, without GitHub or macOS minutes:

```powershell
node --test tests/test_app_launch.mjs
```

The private HA card may also be included:

```powershell
$env:PRESENCE_PEOPLE_CARD='<HA config>\www\smart_presence\smart-presence-tools-card.js'
node --test tests/test_app_launch.mjs
```

Tests cover iPhone/iPad/desktop/Android, complete payload preservation, invalid
schemes, fixed secret-free store URLs, untouched HA navigation, no timer-based
redirects, null native window handles, background tabs, click/polling behavior,
and unavailable invitations.

Physical acceptance still requires checking Safari and the HA Companion app on
an iPhone with Presence Pair installed, then checking the absent-app path on a
test device after the App Store listing is available. Desktop emulation does
not establish native iOS handoff success.
