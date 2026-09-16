#!/usr/bin/env python3
"""Standalone Linux MQTT receiver using the integration's shared backend."""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import signal
import ssl
import sys
import types
from pathlib import Path

import paho.mqtt.client as mqtt

# Load only the portable package, without importing Home Assistant's __init__.
component = (
    Path(__file__).resolve().parents[2] / "custom_components" / "presence_bridge"
)
namespace = types.ModuleType("presence_bridge")
namespace.__path__ = [str(component)]
sys.modules["presence_bridge"] = namespace

from presence_bridge.receiver.bluez import BlueZ  # noqa: E402
from presence_bridge.receiver.engine import LinuxReceiver  # noqa: E402
from presence_bridge.receiver.keys import BlueZKeys, ReceiverError  # noqa: E402

LOGGER = logging.getLogger("presence_bridge.linux")


async def doctor(config):
    transport = BlueZ(config.get("adapter", ""))
    try:
        await transport.connect()
        await asyncio.to_thread(
            BlueZKeys(
                Path(config.get("bluez_storage", "/var/lib/bluetooth")),
                transport.adapter_address,
            ).check_access
        )
        print(
            json.dumps(
                {
                    "ready": True,
                    "platform": "linux",
                    "experimental": True,
                    "checks": [
                        "dbus",
                        "single_powered_adapter",
                        "advertising_api",
                        "bond_storage_readable",
                    ],
                    "not_tested": [
                        "physical_pairing",
                        "simultaneous_scan_advertise",
                        "iPhone",
                    ],
                }
            )
        )
    finally:
        await transport.close()


async def run(config):
    loop = asyncio.get_running_loop()
    queue = asyncio.Queue(maxsize=64)
    connected = asyncio.Event()
    stop = asyncio.Event()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, stop.set)
    transport = BlueZ(config.get("adapter", ""))
    await transport.connect()
    observer_id = "linux_" + transport.adapter_address.replace(":", "").lower()
    await transport.close()
    base = f"presence_bridge/v1/observers/{observer_id}"
    settings = config["mqtt"]
    if not settings.get("username") or not settings.get("password"):
        raise ReceiverError("Configure a dedicated MQTT username and password")
    client = mqtt.Client(
        mqtt.CallbackAPIVersion.VERSION2, client_id=f"presence-bridge-{observer_id}"
    )
    client.username_pw_set(settings["username"], settings["password"])
    if settings.get("tls", False):
        client.tls_set(
            ca_certs=settings.get("ca_file"), tls_version=ssl.PROTOCOL_TLS_CLIENT
        )
    client.will_set(base + "/status", json.dumps({"online": False}), qos=1, retain=True)
    client.max_queued_messages_set(64)

    def on_connect(_client, _userdata, _flags, reason, _properties):
        if reason.is_failure:
            LOGGER.error("MQTT connection rejected")
            return
        _client.subscribe(base + "/pairing/command", qos=1)
        loop.call_soon_threadsafe(connected.set)

    def on_disconnect(_client, _userdata, _flags, _reason, _properties):
        loop.call_soon_threadsafe(connected.clear)

    def enqueue(payload):
        if not queue.full():
            queue.put_nowait(payload)

    def on_message(_client, _userdata, message):
        if (
            not message.retain
            and message.topic == base + "/pairing/command"
            and len(message.payload) <= 8192
        ):
            try:
                payload = json.loads(message.payload)
            except (ValueError, UnicodeError):
                return
            loop.call_soon_threadsafe(enqueue, payload)

    def emit(suffix, payload):
        if not connected.is_set():
            if suffix.startswith("pairing/"):
                raise ReceiverError(
                    "MQTT disconnected; Home Assistant cannot confirm this pairing"
                )
            return
        result = client.publish(
            base + "/" + suffix,
            json.dumps({**payload, "observer_id": observer_id}),
            qos=1,
            retain=suffix == "status",
        )
        if result.rc != mqtt.MQTT_ERR_SUCCESS:
            raise ReceiverError("MQTT publish failed")

    client.on_connect, client.on_disconnect, client.on_message = (
        on_connect,
        on_disconnect,
        on_message,
    )
    receiver = LinuxReceiver(
        transport,
        emit,
        Path(config.get("state_file", "/var/lib/presence-bridge/peers.json")),
        Path(config.get("bluez_storage", "/var/lib/bluetooth")),
    )
    receiver.name = str(config.get("name", "Linux Bluetooth"))[:100]
    client.connect_async(
        settings["host"],
        int(settings.get("port", 8883 if settings.get("tls") else 1883)),
        keepalive=30,
    )
    client.loop_start()
    worker = None
    try:
        await asyncio.wait_for(connected.wait(), 25)
        await receiver.start()

        async def commands():
            while True:
                payload = await queue.get()
                try:
                    await receiver.command(payload)
                except Exception:
                    LOGGER.warning(
                        "Receiver rejected a command; secret payload omitted"
                    )

        worker = asyncio.create_task(commands())
        LOGGER.info("Linux receiver online (experimental); waiting for Home Assistant")
        await stop.wait()
    finally:
        if worker:
            worker.cancel()
            await asyncio.gather(worker, return_exceptions=True)
        await receiver.close()
        client.disconnect()
        client.loop_stop()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--config", type=Path, default=Path("/etc/presence-bridge/config.json")
    )
    parser.add_argument(
        "--doctor",
        action="store_true",
        help="Read-only platform checks; never starts pairing",
    )
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    try:
        config = json.loads(args.config.read_text(encoding="utf-8"))
        asyncio.run(doctor(config) if args.doctor else run(config))
    except Exception as error:
        print(
            str(error)
            if isinstance(error, ReceiverError)
            else f"Receiver failed ({type(error).__name__}); check configuration and Linux setup",
            file=sys.stderr,
        )
        raise SystemExit(1) from None


if __name__ == "__main__":
    main()
