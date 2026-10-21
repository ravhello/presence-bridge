# Contributing

Bug reports and focused pull requests are welcome.

## Getting Started

Fork the public repository and work on a feature branch. Install Python 3.11+
and Node.js 20+, create a virtual environment and install `requirements-dev.txt`.
Run `python -m pytest`, `python -m ruff check .`,
`python -m ruff format --check .`, then
`node --test tests/test_app_launch.mjs tests/test_setup_panel.mjs`.
Windows-specific runtime tests run on Windows. POSIX BlueZ storage tests run
in Linux CI; skipping them on Windows is not proof they passed on Linux.

`tests/test_service_security.py` also uses the real HA service registry/helper
when Home Assistant is installed. The `ha-permissions` CI job installs pinned
HA 2026.3.2 on Python 3.14 and must run those tests without skips. A receiver-only
venv skips the HA-specific cases; passing its contract tests alone is not proof
that the real admin permission boundary was exercised. These isolated tests do
not load a home configuration, connect to a radio or restart a running HA.

Use standard public GitHub-hosted runners only; no paid macOS jobs are needed
for this repository. The proprietary iOS app is not built here.

## New Receivers And Hardware Reports

Read [architecture](docs/architecture.md), [protocol](docs/protocol.md) and
[compatibility](docs/compatibility.md). Keep an OS backend isolated from HA.
Add behavior tests for authentication failure, stale bonds, duplicate commands,
cancellation, HA restart, removal and original observation timestamps.

Open a hardware report with OS/BlueZ or driver version, adapter model, CPU
architecture, app build and which scenarios you physically tested. Never include
MAC addresses, real keys, QR codes, personal names or unfiltered logs. A successful
scan alone is not a successful enrollment. Separate fresh/saved/asymmetric-bond
results and verify the app receipt and HA persistence.

Changes must not publish app code, remove other Bluetooth devices, weaken bond
security or reinterpret missing reception as confirmed absence. There is no
contributor license assignment: accepted integration contributions remain MIT.

1. Do not include real IRKs, Bluetooth addresses, MQTT credentials, Home
   Assistant backups, or pairing invitations in issues or fixtures.
2. Run `python -m ruff check .`, `python -m ruff format --check .`, and
   `python -m pytest` before opening a pull request.
3. Keep protocol changes backward compatible or introduce an explicit version.
4. Test Windows GATT changes from both an administrator session and the
   installed SYSTEM task.

Presence Pair is a separate proprietary iPhone application. Contributions in
this repository apply only to the free integration, observer, protocol, and
documentation.
