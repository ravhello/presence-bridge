# Release candidate 0.1.36: validation record

## Verified locally on 2026-09-14

- 180 Python tests pass, including saved-bond handling, authenticated ACK gates,
  exact-phone removal, stale radio data, native HA ingestion and Person linking.
- Scoped signal API tests cover single-use redemption, replacement without
  premature revocation, isolation between phones, concurrent requests and
  service outage/reload without loss of stored credentials.
- Ruff lint and formatting pass. The HA panel passes JavaScript syntax checks.
- Windows PowerShell 5.1 installer ACL tests pass: SYSTEM code and commands are
  not writable by the interactive pairing user; only pairing data is writable.
- `tools/build-release.ps1` produces the component and Windows archives and
  invokes `tools/test-release.ps1`. All 23 component and 13 receiver files match
  their tested source hashes, required modules are present and private/live
  configuration, diagnostic launchers and keys are excluded.

## Read-only runtime checks

HA 2026.8.3 reports the integration loaded, one persisted identity linked to its
Person without replacing the existing mobile tracker, and three online passive
receivers. Current phone samples were observed. The Windows observer is running;
the pairing helper is idle. Its installed 0.1.35 source matches the 0.1.36
candidate apart from the version label. No working bond, grant or task was reset.

## Not established by these checks

The final public-flow fresh pairing video, the new iOS monitor's physical
Keychain/TLS test, a clean install on another Windows host, and a wider adapter/
phone matrix remain hardware gates. Unit tests and a working saved identity do
not certify those cases. Native HA radios support passive observations, not
initial iPhone enrollment. Keep this release a candidate until the physical
demonstration passes; do not relabel it universally compatible.

Bluetooth pairing does not require Wi-Fi on the iPhone. The Windows receiver
needs HA/MQTT connectivity. The separate HTTPS monitor requires a network route
from the iPhone to HA and must not treat a network failure as Bluetooth removal.
