---
title: Linux Receiver (Experimental)
permalink: /linux/
---

# Linux Receiver (Experimental)

This release includes working BlueZ transport and enrollment code, tests with
fake Bluetooth hardware, and a Linux CI job. Physical Linux/iPhone certification
is still pending. Do not describe a passing unit test as an adapter certification.

## Requirements

- Linux, BlueZ 5.x, Python 3.11+ for the standalone service.
- A powered BLE adapter supporting central connections and advertising.
- Access to the system D-Bus socket and BlueZ method permissions.
- Read-only access to `/var/lib/bluetooth/<adapter MAC>/<phone MAC>/info`.
- Presence Pair on iOS 17+; local mode additionally needs HA's Bluetooth integration.
- MQTT only for the standalone/remote receiver.

BlueZ's standard D-Bus API does not export IRKs. The receiver reads only the
QR-verified peer's file and requires an authenticated 128-bit LongTermKey.
It never copies the whole bond database, writes BlueZ files, registers a default
agent or accepts unrelated pairing requests. These files are privileged secrets:
only mount them into a trusted HA/receiver container, read-only.

## Inside HA Container (No MQTT)

Configure Bluetooth on the Linux host using HA's
[official Bluetooth requirements](https://www.home-assistant.io/integrations/bluetooth/).
Expose system D-Bus as documented there. Additionally mount the selected adapter
directory read-only, for example:

```yaml
volumes:
  - /run/dbus:/run/dbus:ro
  - /var/lib/bluetooth/AA:BB:CC:DD:EE:FF:/var/lib/bluetooth/AA:BB:CC:DD:EE:FF:ro
```

Replace the example MAC with the real adapter MAC. This is not a complete HA
Compose file and does not replace HA's existing volumes or network settings.
Permissions must permit reading the selected files. Do not make the host
Bluetooth directory world-readable.

Install Presence Bridge, select **Local Linux receiver**, and set that adapter
MAC in integration options. The panel reports missing D-Bus, power, advertising
or storage access. Fix prerequisites and reload the integration. No restart of
the computer/radio and no MQTT are needed.

On HA OS, host key storage is not generally exposed to Core. Do not modify the
managed OS or disable its protections: use the remote receiver below. A dedicated
HA OS add-on remains a future port, not an advertised feature.

## Standalone systemd Receiver

Use a dedicated Linux machine or the Linux host itself. Install distro packages
`bluez`, `python3`, `python3-venv` and `python3-pip`.
Download the Linux release archive and extract it, preserving its directory tree.

```sh
sudo sh bridge/linux/install.sh
sudoedit /etc/presence-bridge/config.json
sudo /opt/presence-bridge/.venv/bin/python /opt/presence-bridge/bridge/linux/receiver.py --doctor
sudo systemctl enable --now presence-bridge
sudo systemctl status presence-bridge --no-pager
```

The installer creates files but deliberately does not start with placeholder
credentials. Configure MQTT host, dedicated username/password and optional TLS
CA file. Select a stable adapter MAC when more than one radio exists.
The doctor command is read-only: passing it is not a pairing test.

The systemd service runs without a desktop session. Root is used for BlueZ key
file access and D-Bus policy; the unit has no Linux capabilities, no new
privileges, a read-only system, and a private writable state directory.
It never requires an SSH service or public listener.

## Docker Receiver

From the extracted Linux release:

```sh
cp bridge/linux/config.example.json bridge/linux/config.json
chmod 600 bridge/linux/config.json
# Edit config.json with your broker and adapter.
docker compose -f bridge/linux/compose.yaml build
docker compose -f bridge/linux/compose.yaml run --rm presence-bridge --config /etc/presence-bridge/config.json --doctor
docker compose -f bridge/linux/compose.yaml up -d
```

The supplied Compose uses host networking, a read-only filesystem, no Linux
capabilities, read-only D-Bus and BlueZ storage, and a persistent identity map.
It does not use `privileged: true`. The host, not the container, runs BlueZ.
Narrow the Bluetooth storage mount to the selected adapter when possible.

## Known Limits And Recovery

- Both already-bonded and new phones follow the same QR/HMAC and protected-write
  checks. Existing healthy bonds are reused; no popup is required in that case.
- Stale asymmetric bonds can be rejected by iOS/BlueZ. Linux does not silently
  delete bonds on a timeout. Remove this association through HA, forget the
  receiver on the iPhone if needed, and retry. Unrelated devices are never reset.
- BlueZ sometimes exposes a resolved identity address rather than the radio RPA.
  HA accepts that address only from its enrolling receiver and only after the
  authenticated encrypted result. Other receivers still need genuine RPA
  resolution. Cached RSSI never becomes a fresh sample merely because of polling.
- After a BlueZ/adapter disconnect, reload the integration or restart only the
  standalone service. No automatic USB/radio reset is attempted.
- Apple advertising intervals and RF conditions limit RSSI refresh frequency.
- ARM and x86 packaging is architecture-neutral Python, but each radio/OS/iOS
  combination still needs the [hardware checklist](compatibility.md).

## Upgrade And Uninstall

The installer refuses to overwrite an existing installation. For an upgrade:
stop `presence-bridge`, back up `/opt/presence-bridge`,
`/etc/presence-bridge` and `/var/lib/presence-bridge`; install the new release
into a new directory and update the unit's ExecStart after its doctor check.
Preserve the configuration and private identity map. Roll back ExecStart and
the HA integration version together if necessary.

To uninstall, first remove phones through HA if you want their bonds removed,
then disable/stop the unit. Remove only its own unit, installation, configuration
and state directories after checking their paths. Never delete
`/var/lib/bluetooth` or restart Bluetooth as a cleanup shortcut.
