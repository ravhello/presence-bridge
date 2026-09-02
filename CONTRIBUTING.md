# Contributing

Bug reports and focused pull requests are welcome.

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
