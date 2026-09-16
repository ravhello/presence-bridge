# Security policy

Please report vulnerabilities through the repository's private GitHub Security
Advisories. Do not open a public issue containing a real IRK, private Bluetooth
address, MQTT credential, Home Assistant backup, or active pairing code.

Supported security fixes target the latest published release. The pairing
protocol and threat model are documented in [docs/protocol.md](docs/protocol.md).

Linux 0.2.0 is an experimental trusted-host backend, not a sandbox against a
malicious host administrator. Its BlueZ bond mount is read-only, but contains
sensitive keys: mount only the selected adapter and never expose it over a
network share or in a public container image. Local mode keeps protocol events
in HA. Remote mode requires a trusted MQTT broker, dedicated credentials/topic
ACLs and TLS outside trusted local segments. Never open system D-Bus remotely.

Private vulnerability reporting is enabled on this repository. Review a support
report before sending it; no automatic diagnostic upload is performed by the
receivers. Do not include the BlueZ info file, receiver config, private identity
map, packet capture, QR invitation or Home Assistant storage in a public issue.

Presence is an estimate, not a security authorization signal. Keep physical
alarm, lock and safety decisions independent of this preview's room inference.
