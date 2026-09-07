#!/usr/bin/env python3
"""Recover a confirmed Windows BLE bond into an active HA pairing session.

This utility is intentionally narrow: it runs as LOCAL SYSTEM, resolves one
observed private address against Windows' stored IRKs, then waits for a fresh
Presence Bridge pairing command and returns that identity through the normal
ephemeral RSA/MQTT channel. It never writes Home Assistant storage directly.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import threading
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import paho.mqtt.client as mqtt
from observer import (
    ObserverConfig,
    encrypt_pairing_result,
    mqtt_reason_is_failure,
    normalize_address,
    read_windows_private_ble_irks,
    select_irk_record_for_address,
)

SESSION_ID_RE = re.compile(r"^[A-Za-z0-9_-]{16,96}$")


def write_status(path: Path, **values: Any) -> None:
    """Atomically persist diagnostics without private identity material."""
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(values, separators=(",", ":"), sort_keys=True),
        encoding="utf-8",
    )
    temporary.replace(path)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--address", required=True)
    parser.add_argument("--status", type=Path, required=True)
    parser.add_argument("--timeout", type=int, default=120)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    config = ObserverConfig.load(args.config)
    address = normalize_address(args.address)
    timeout = min(300, max(30, int(args.timeout)))
    if not address:
        write_status(args.status, state="error", error="invalid_address")
        return 2

    record = select_irk_record_for_address(
        read_windows_private_ble_irks(),
        address,
    )
    if record is None:
        write_status(args.status, state="error", error="identity_not_resolved")
        return 3

    command_topic = f"{config.topic_root}/{config.observer_id}/pairing/command"
    result_topic = f"{config.topic_root}/{config.observer_id}/pairing/result"
    status_topic = f"{config.topic_root}/{config.observer_id}/pairing/status"
    completed = threading.Event()
    outcome: dict[str, Any] = {"state": "waiting", "matched": True}

    client = mqtt.Client(
        callback_api_version=mqtt.CallbackAPIVersion.VERSION2,
        client_id=f"presence-recovery-{config.observer_id}-{os.getpid()}",
        protocol=mqtt.MQTTv311,
    )
    if config.mqtt.username:
        client.username_pw_set(config.mqtt.username, config.mqtt.password)

    def on_connect(
        connected_client: mqtt.Client,
        _userdata: Any,
        _flags: Any,
        reason_code: Any,
        _properties: Any,
    ) -> None:
        if mqtt_reason_is_failure(reason_code):
            outcome.update(state="error", error="mqtt_connection_rejected")
            write_status(args.status, **outcome)
            completed.set()
            return
        connected_client.subscribe(command_topic, qos=1)
        outcome.update(state="ready")
        write_status(args.status, **outcome)

    def on_message(
        connected_client: mqtt.Client,
        _userdata: Any,
        message: Any,
    ) -> None:
        try:
            payload = json.loads(message.payload.decode("utf-8"))
            if payload.get("action") != "start_app_pairing":
                return
            session_id = str(payload.get("session_id") or "")
            observer_id = str(payload.get("observer_id") or "")
            expires_at = int(payload.get("expires_at") or 0)
            public_key = str(payload.get("public_key") or "")
            if (
                not SESSION_ID_RE.fullmatch(session_id)
                or observer_id != config.observer_id
                or expires_at <= int(time.time()) + 10
            ):
                raise ValueError("invalid_pairing_command")
            encrypted = encrypt_pairing_result(
                public_key,
                {
                    "irk": record["irk"],
                    "matched_address": normalize_address(record.get("registry_leaf")),
                    "captured_at": datetime.now(UTC).isoformat(),
                    "claim_verified": True,
                    "recovery": "confirmed_windows_bond",
                },
            )
            result = {
                "schema": 2,
                "observer_id": config.observer_id,
                "session_id": session_id,
                "ciphertext": encrypted,
            }
            publish = connected_client.publish(
                result_topic,
                json.dumps(result, separators=(",", ":")),
                qos=1,
                retain=False,
            )
            publish.wait_for_publish(timeout=10)
            progress = {
                "schema": 2,
                "observer_id": config.observer_id,
                "session_id": session_id,
                "state": "identity_captured",
                "message": "Confirmed Windows bond recovered; Home Assistant is verifying it",
                "updated_at": datetime.now(UTC).isoformat(),
                "detail_code": "confirmed_windows_bond_recovered",
                "transport": "iphone_peripheral",
            }
            connected_client.publish(
                status_topic,
                json.dumps(progress, separators=(",", ":")),
                qos=1,
                retain=False,
            ).wait_for_publish(timeout=10)
            outcome.update(state="published", session_id=session_id)
        except Exception as error:
            outcome.update(state="error", error=type(error).__name__)
        finally:
            write_status(args.status, **outcome)
            completed.set()

    client.on_connect = on_connect
    client.on_message = on_message
    try:
        client.connect(config.mqtt.host, config.mqtt.port, keepalive=30)
        client.loop_start()
        if not completed.wait(timeout):
            outcome.update(state="error", error="pairing_command_timeout")
            write_status(args.status, **outcome)
            return 4
        return 0 if outcome.get("state") == "published" else 5
    finally:
        client.disconnect()
        client.loop_stop()


if __name__ == "__main__":
    raise SystemExit(main())
