# Changelog

## 0.1.36 - public preview (2026-09-14)

- Live per-receiver RSSI with real packet timestamps and 45-second freshness.
- Latest rotated-address measurement replaces historical maximum RSSI.
- Separate, revocable read-only signal QR/API for the iOS live monitor; no new
  Bluetooth pairing, keys or Home Assistant control permissions.
- Native HA scanner cache refresh at 5 seconds. External bridge intervals remain
  unchanged. Precise distance in meters is not claimed from RSSI.
- Published as an opt-in GitHub pre-release with matching HA and Windows
  archives. The final public-flow video and broader hardware matrix remain
  pending; this does not publish the iPhone app on the App Store.

All notable changes to Presence Bridge are documented here.

## 0.1.35 - release candidate, hardware gates pending

- Use QR-scoped authenticated Windows pairing in the standard app flow, with
  no diagnostic arm file or operator window. Keep the exact-session diagnostic
  override for controlled fresh tests. Reuse only verified authenticated bonds
  and still require the protected app acknowledgement.
- Recover a one-sided forgotten bond at most once, only for a QR-verified peer
  with an existing authenticated bond and an explicit ATT encryption rejection.
  Never remove a bond because of a timeout, a weak signal or generic disconnect.
- Receive passive presence through HA's native Bluetooth cache and supported
  proxies, including non-connectable scanners. No active radio connection or
  additional scanner is opened for presence tracking.
- Preserve actual advertisement timestamps across MQTT snapshots and cache
  polling; expire offline data and avoid stale room labels. Add room hysteresis.
- Link an identity's tracker to its selected editable Person without replacing
  existing GPS/Wi-Fi trackers or stealing another person's device.
- Missing BLE reception now yields an unknown tracker state, not proven away.
  All-receiver outages make identity entities unavailable.
- Keep selection menus open during panel polling and redact the new last-known
  area field from diagnostics. See docs/compatibility.md for tested boundaries.

The private fresh 0.1.34 physical test completed authenticated pairing,
protected GATT acknowledgement, HA persistence and phone completion. A standard
0.1.35 public-flow hardware retry and the wider matrix remain release gates.

## 0.1.34 - diagnostic candidate

- Re-enumerate the exact Windows peer after numeric pairing succeeds and verify
  its current paired state and authenticated protection level. The 0.1.33 live
  test returned PAIRED with stale result protection NONE, while a fresh Windows
  query confirmed ENCRYPTION_AND_AUTHENTICATION. The old gate stopped enrollment
  before attempting the protected app acknowledgement.
- Do not treat a successful pairing status alone as enrollment. Weak/unpaired
  peers and failed verification remain errors; the protected GATT exchange and
  Home Assistant identity commit are still required and await a new live test.

## 0.1.33 - diagnostic candidate

- Add owner-preauthorized receiver consent for one exact diagnostic QR session,
  only after the app proves possession of that QR. The iPhone must still accept
  the Bluetooth request; protected GATT acknowledgement and HA commit remain
  mandatory. No visual PIN comparison is claimed for this opt-in mode.
- This is not default consent for other devices or public proof of compatibility.
  The previous numeric-comparison tests expired awaiting user confirmations.

## 0.1.32 - diagnostic candidate

- Add an opt-in, one-QR numeric-comparison experiment. Request authenticated
  encryption before the protected acknowledgement; never fall back to Just Works
  or accept a comparison PIN without explicit, nonce-bound human confirmation.
- Do not change normal enrollment when the diagnostic is not armed. Preserve
  existing bonds on failure and require the actual protected app acknowledgement
  before Home Assistant can commit an identity.
- The encrypted ATT authentication rejection remains unproven as resolved.
  This version is prepared for a physical comparison test, not public release.

## 0.1.31 - release candidate

- Stop immediately if the stronger GATT authentication request fails. Windows
  may return Unreachable locally without sending another ATT request; retrying
  that request did not repair the authenticated channel during physical tests.
- Bound all post-bond protected-write recovery attempts, including non-ATT
  errors, to three attempts per phone. Preserve the bond and reject enrollment.
- Receiver 0.1.30 failed physical fresh enrollment. Public release remains on
  hold; this change fixes the retry regression, not the underlying iPhone
  authentication rejection.

## 0.1.30 - release candidate

- On ATT insufficient authentication after bonding, request encrypted and
  authenticated GATT access once before repeating the same connection. Keep
  that stronger requirement on reconnect, bound it by the existing deadline,
  preserve cancellation and require an actual successful protected write.

- Add a QR-scoped failure receipt for app-aware receivers. Stop progress
  heartbeats before publishing a terminal Bluetooth error, then advertise a
  domain-separated negative receipt within the existing attempt deadline.
  This never substitutes for encrypted acknowledgement or creates an identity.
- The corresponding Presence Pair app update is required to display the error
  immediately. The receiver changes alone cannot update an installed app.
- Driver evidence confirms link encryption was active before ATT 0x05. Physical
  validation of the stronger authentication recovery is still required; local
  protocol tests are not evidence of successful enrollment.

## 0.1.29 - release candidate

- After an unreadable session or claim, force fresh discovery for that phone
  rather than repeatedly accepting its cached service/characteristic list.
  Read operations have a ten-second bound and preserve cancellation and bonds.
- Distinguish terminal link/encryption errors from actual pairing deadlines in
  the helper and Home Assistant status. Neither condition commits an identity.
- Physical fresh enrollment and saved-bond reuse remain release gates.

## 0.1.28 - release candidate

- Add an explicit fresh-pairing test reset utility. Verify exact-phone Windows
  bond/key absence before a test QR; keep normal saved-bond enrollment unchanged.
- Restore Bleak's default, cancellable WinRT service discovery. The optional
  internal Services Changed retry loop could leave child discovery requests
  pending after a route timed out.
- Limit modern iPhone discovery to the QR-selected service; prefer an uncached
  targeted lookup initially and a cached targeted lookup after a confirmed bond.
  Do not enumerate unrelated Apple services or pair again during discovery.
- Preserve saved bonds on discovery timeouts and missing services. Only the
  QR-verified secure-exchange error path may request targeted bond repair.
- Bound failed post-bond rediscovery to two clean attempts and report a precise
  incomplete-link error rather than cycling through every cache permutation.
- Keep GATT timing and link metadata, but omit protocol payloads from debug
  logging and preserve the actual protected-write error status.
- Physical confirmation remains required before publication.

## 0.1.27 - release candidate

- Explicitly require WinRT GATT link encryption before writing the verified
  iPhone acknowledgement, preserving any stronger authentication requirement.
- Stop after three repeated ATT authentication/encryption rejections for the
  same phone instead of reconnecting until the five-minute deadline. A saved
  Windows bond alone still cannot complete enrollment.
- Surface a terminal encryption diagnostic without automatically erasing a
  bond or requesting repeated pairing consent.
- Consume completed helper cleanup tasks as well as cancelled pending tasks
  so cleanup exceptions cannot obscure the original Bluetooth failure.
- Windows receiver deployed without restarting HA; 102 local tests pass.
  Fresh pairing and saved-bond reuse remain mandatory physical release gates.

## 0.1.26 - release candidate

- Removing an identity now unpairs the phone on the Windows receiver that
  enrolled it, then clears HA only after Windows verifies bond and key absence.
- Match the exact private identity and its physical Windows container to remove
  both BLE and classic endpoints without selecting unrelated devices by name.
- Use the signed-in Bluetooth broker for BLE removal and a native desktop API
  fallback for remembered devices; no manual Windows interaction is needed.
- Preserve HA associations on offline receivers, errors and timeouts. Journal
  exact unfinished removal targets so partial failures can be retried safely.
- Reject retained, expired, wrong-receiver and mismatched removal messages;
  duplicate requests are idempotent and no private key is sent in the command.
- Update removal confirmations and document clean test setup on both endpoints.
- Server-side unpair was physically verified; fresh and reused-bond pairing
  remain release gates inherited from 0.1.25.

## 0.1.25 - release candidate

- Require a successful encrypted iPhone acknowledgement before exporting an
  identity or transmitting Home Assistant's completion receipt. A matching
  saved Windows IRK alone is no longer a successful pairing.
- Try two bounded discovery probes before the existing QR-scoped, one-peer
  stale-bond recovery; retain full compatibility retries after that recovery.
- Explicitly release WinRT reconnection requests even when GATT is already
  disconnected; log pairing phase timings without invitation secrets.
- Hold publication pending a physical forget/re-pair test of these changes.
- Prepare the free public compatibility release for Presence Pair build 214.
- Check both BLE central and peripheral roles before accepting an adapter.
- Limit the interactive pairing account to read-only access to SYSTEM code;
  isolate writable exchange files in a private `pairing` directory.
- Refuse receiver updates while an enrollment is running.
- Validate the installation path before either uninstall mode and remove the
  current interactive helper task as well as the observer task.
- Correct installation prerequisites, signed-in-session requirements and
  troubleshooting instructions; add a privacy-first bug-report form.

## 0.1.24 - 2026-09-10

- Bound an accepted iPhone scan to a single five-minute attempt, including
  proximity, retries and final Home Assistant verification.
- Confirm persisted Home Assistant identities with a session-authenticated BLE
  receipt, including reuse of an existing Windows/iPhone bond.

## 0.1.23 - 2026-09-10

- Preserve a pairing attempt for thirty minutes as soon as the iPhone confirms
  the QR-derived proximity beacon, even when that happens seconds before the
  original invitation expires.
- Propagate the handoff lease through the Windows helper, observer, MQTT state,
  and Home Assistant UI so an in-flight connection cannot be cancelled by a
  missed intermediate status update.

## 0.1.22 - 2026-09-09

- Stop the QR proximity beacon as soon as the iPhone's final session
  advertisement is visible, avoiding a preliminary Bluetooth bond and duplicate
  iOS pairing prompts.

## 0.1.21 - 2026-09-09

- Recreate the Windows proximity GATT provider when a freshly renewed QR races
  with delayed Bluetooth adapter cleanup, keeping the same session alive while
  the receiver retries automatically.

## 0.1.20 - 2026-09-09

- Keep the encrypted Home Assistant identity payload below the RSA-OAEP limit
  after an existing Windows bond is recovered.
- Let the post-bond GATT channel settle and reconnect with the confirmed bond
  before falling back to local IRK completion.
- Report an explicit payload-capacity error instead of the opaque
  `Encryption failed` exception.

## 0.1.19 - 2026-09-09

- Advertise a QR-scoped Dell proximity service before secure iPhone pairing.
- Let Presence Pair measure the receiver signal and delay pairing until the
  phone is close enough, while preserving the direct path for existing builds.
- Start the encrypted exchange automatically after authenticated proximity
  confirmation, without requiring a Windows prompt.

## 0.1.18 - 2026-09-09

- Reuse an existing QR-matched Windows BLE bond before requesting another pairing.
- Preserve an accepted QR attempt while waiting for a stable local radio link.
- Report weak iPhone RSSI explicitly instead of repeatedly opening an unusable GATT route.

## 0.1.17 - 2026-09-07

- Keep a QR code valid for ten minutes only as an invitation to start pairing.
  Once the matching iPhone session is read, consume the invitation and grant a
  separate thirty-minute window so its original expiry cannot interrupt the bond.
- Show separate start, handoff, and completion countdowns in Home Assistant,
  and remove the QR as soon as the authenticated session takes ownership.

## 0.1.16 - 2026-09-07

- Preserve a newly accepted Windows/iPhone bond while WinRT settles and retry
  the authenticated claim without showing a second pairing prompt.
- Avoid hiding the dynamic iOS GATT service behind characteristic-level link
  encryption; the one-time HMAC claim still authenticates both endpoints.

## 0.1.15 - 2026-09-07

- Use the Dell GATT-peripheral route for the currently distributed iPhone
  build, and keep the receiver scanner paused so one adapter never performs
  incompatible Bluetooth roles concurrently.

## 0.1.14 - 2026-09-07

- Accept the truncated `Presence` local name that iOS can emit when the
  128-bit pairing service consumes the main Bluetooth advertisement.

## 0.1.13 - 2026-09-07

- Support both GATT roles while selecting exactly one from the invitation
  protocol, so a single-adapter receiver never blocks its own Bluetooth scan.
- Keep the compatibility server available for older invitations while routing
  current protocol-v2 app sessions through the iPhone peripheral transport.
- Retry transient WinRT adapter loss during reverse scanning instead of ending
  the complete QR session immediately.
- Detect the temporary iPhone peripheral from its service list, service data,
  or exact Presence Pair local name when either platform omits redundant
  advertisement fields.

## 0.1.11 - 2026-09-07

- Run the short-lived WinRT GATT exchange in the logged-in user's Bluetooth
  session while retaining the always-on scanner and protected IRK extraction
  under the LocalSystem observer.
- Install a bounded on-demand pairing task with atomic, access-controlled local
  command and progress files for unattended receiver operation.
- Enable detailed local Bleak client diagnostics only in the short-lived helper.

## 0.1.10 - 2026-09-07

- Prefer Windows' native GATT cache policy and add a pair-before-discovery
  fallback for iPhone services whose protected characteristics prevent initial
  enumeration.
- Clear a matching stale Presence Pair bond once after all compatible WinRT
  routes fail, then retry the same active QR invitation.
- Log the matching advertisement's address type and radio strength so receiver
  failures can be separated from iPhone advertising failures.

## 0.1.9 - 2026-09-06

- Keep receiver health and current progress alive while an app-assisted GATT
  pairing temporarily pauses passive scans.
- Cancel orphaned receiver sessions automatically after a Home Assistant
  restart, when their in-memory encryption key can no longer be recovered.
- Include the receiver's friendly name in new pairing invitations while
  retaining the stable technical identifier on the wire.

## 0.1.8 - 2026-09-06

- Recover automatically when a stale Windows/iPhone bond blocks GATT service
  discovery before the active QR session can be read.

## 0.1.7 - 2026-09-06

- Connect to iPhone peripheral advertisements with their WinRT random address
  type and bypass stale Windows GATT service caches.
- Retry connection through bounded service-discovery fallbacks and replace a
  stale bond only after the phone proves it scanned the active QR invitation.
- Treat pairing progress as an observer heartbeat so the receiver remains
  online while the normal BLE scan is intentionally paused.
- Add detailed, privacy-safe connection diagnostics for future adapter and
  driver failures.

## 0.1.6 - 2026-09-05

- Reverse the enrollment transport so Presence Pair temporarily advertises the
  protected GATT service and Windows initiates the connection as BLE central.
- Authenticate both the iPhone claim and the receiver acknowledgement in
  protocol v2, while keeping the QR secret and IRK off the Bluetooth wire.
- Reuse an existing Windows bond by matching its identity address or resolvable
  private address, instead of requiring a newly created registry entry.
- Report distinct discovery, connection, session, bond, claim, and identity
  phases in Home Assistant and in the iPhone app.
- Remove the obsolete signed Windows peripheral host from new installations.

## 0.1.5 - 2026-09-05

- Pause the Windows BLE presence scanner during pairing and resume it
  automatically, leaving the adapter free to accept the incoming iPhone GATT
  connection.
- Ignore duplicate MQTT starts for the same active QR session and stale cancel
  messages for older sessions, so a valid GATT advertisement is never replaced
  while the iPhone is connecting.
- Accept a claim that arrives before the controller's first advertising poll,
  avoiding a false startup timeout on fast Bluetooth exchanges.
- Launch the signed GATT host in the installing user's active desktop session;
  Windows session-zero advertising can be visible while service discovery
  still times out on iPhone.
- Verify the adapter's peripheral role and keep the default legacy connectable
  advertisement; enabling a secondary PHY creates a second extended record on
  this Intel adapter that clients can discover but cannot enumerate reliably.
- Stop and replace the signed Windows GATT host gracefully so repeated QR-code
  renewals cannot leave the Bluetooth stack advertising an unreachable service.
- Report each client milestone from session discovery through encrypted claim,
  making stale QR codes and radio failures distinguishable in HA and local logs.
- Keep the session and encrypted result as static GATT values so service
  enumeration remains reliable across Windows and iOS Bluetooth stacks.

## 0.1.4 - 2026-09-05

- Run the connectable Windows GATT service from a signed identity-bearing host,
  while keeping passive BLE scanning in the resilient SYSTEM observer.
- Exchange pairing commands and diagnostics atomically between the two
  processes, with explicit failures for host identity, task startup, radio,
  iPhone connection, and timeout stages.
- Reuse an active invitation by default so multiple HA or QR-page tabs cannot
  silently cancel the code already scanned by the iPhone.
- Install, verify, and remove the sparse Windows identity and its dedicated
  on-demand task as part of the observer lifecycle.

## 0.1.3 - 2026-09-05

- Verify the real Windows GATT advertising state before reporting the receiver
  as ready, retry transient advertising failures, and expose radio diagnostics
  to Home Assistant.
- Show actionable pairing guidance and one-click QR renewal in the HA panel.
- Include GATT diagnostics in the observer log and adapter verification tool.

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
