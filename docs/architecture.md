# Architecture And Extension Contract

## Components

HA owns people, areas, invitation keys, private identity storage and presence
entities. Presence Pair is an independent iOS client implementing protocol v2.
Receivers own their OS-specific Bluetooth operations. No backend developer
service is needed. The app source/signing keys are not part of this repository.

Local Linux uses the same message contract in-process. Remote Linux/Windows use
MQTT. Passive HA Bluetooth observations are another source, not a pairing
backend. Do not confuse a proxy's connection support with key export capability.

## Linux Layers

- `receiver/keys.py`: bounded, selected-peer, read-only BlueZ identity access.
- `receiver/bluez.py`: D-Bus agent, own discovery reference, advertising, GATT.
- `receiver/engine.py`: bounded protocol state machine, encrypted identity
  transfer, HA completion receipt, scoped removal.
- `local_receiver.py`: HA lifecycle and in-process message routing.
- `bridge/linux/receiver.py`: MQTT adapter and headless process lifecycle.

The Linux agent belongs to its own D-Bus connection. It is never the default
system agent. Confirmation is accepted only for the exact QR-proven peer before
the fixed deadline. Just Works, legacy PIN and unrelated requests are rejected.
A successful protected write and authenticated key file are both required.
The receiver does not infer success from a paired flag.

## Ports And Community Contributions

A new OS backend needs: capability checks, fresh observations with original
timestamps, session-specific advertising, verified GATT reads, authenticated
bonding, protected acknowledgement, selected-peer IRK access, target-only bond
removal and cancellation. Keep this code separate from HA entities and UI.

Reuse protocol test vectors. Do not add a global agent, automatic radio reset,
IRK dump, password in command arguments, repeated pairing loop, telemetry upload
or silent trust downgrade. State which steps are physically verified.

Receiver events use `status`, `observations`, `pairing/status`,
`pairing/result` and `identity_removal/result`; commands use the existing
`presence_bridge/v1/observers/<observer_id>/pairing/command` topic.
Only status may be retained. Commands/results must not be replayed from retained
MQTT messages. A duplicate active command does not create another attempt.

## Compatibility Decisions

The Windows runtime is retained; it is not replaced by the Linux preview.
Linux can run in-process only where host permissions permit. A managed HA OS
add-on, macOS receiver and pairing-capable microcontroller firmware are distinct
future projects, not claims of this release. ESPHome and other passive proxies
can already contribute observations.

Resolved BlueZ identity addresses are stored privately and accepted only from
the enrolling observer, after authenticated result verification. This does not
make an arbitrary MAC address from a different receiver proof of identity.
