# Changelog

All notable changes to Presence Bridge are documented here.

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
