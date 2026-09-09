# Changelog

All notable changes to Presence Bridge are documented here.

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
