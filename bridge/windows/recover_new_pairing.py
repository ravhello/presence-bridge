#!/usr/bin/env python3
"""Finalize one QR-verified iPhone bond when WinRT hides its GATT service.

This temporary compatibility path observes the normal interactive helper. It
only returns an identity after that helper reports that the QR session was
verified and Windows created a new bond. Home Assistant still receives the
identity through its ephemeral RSA/MQTT pairing channel and verifies a live
private advertisement before saving it.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
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
    select_new_irk_records,
)

SESSION_ID_RE = re.compile(r"^[A-Za-z0-9_-]{16,96}$")
BOND_READY_STAGES = {
    "iphone_bond_ready",
    "iphone_bond_settling",
    "iphone_bond_reconnecting",
    "iphone_ack_deferred",
}


def write_status(path: Path, **values: Any) -> None:
    """Atomically persist diagnostics without private identity material."""
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(values, separators=(",", ":"), sort_keys=True),
        encoding="utf-8",
    )
    temporary.replace(path)


def read_progress(path: Path, session_id: str) -> str:
    try:
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return ""
    if str(payload.get("session_id") or "") != session_id:
        return ""
    return str(payload.get("detail_code") or "")


def restart_observer(observer_task: str, helper_task: str) -> None:
    """Stop the finished enrollment loop and immediately resume passive scan."""
    flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
    subprocess.run(
        ["schtasks.exe", "/End", "/TN", helper_task],
        check=False,
        capture_output=True,
        creationflags=flags,
    )
    subprocess.run(
        ["schtasks.exe", "/End", "/TN", observer_task],
        check=False,
        capture_output=True,
        creationflags=flags,
    )
    time.sleep(0.8)
    subprocess.run(
        ["schtasks.exe", "/Run", "/TN", observer_task],
        check=False,
        capture_output=True,
        creationflags=flags,
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--status", type=Path, required=True)
    parser.add_argument("--timeout", type=int, default=180)
    parser.add_argument(
        "--observer-task",
        default="Home Assistant - BLE Presence Observer",
    )
    parser.add_argument(
        "--helper-task",
        default="Presence Bridge - Interactive Pairing Client",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    config = ObserverConfig.load(args.config)
    timeout = min(300, max(60, int(args.timeout)))
    baseline = read_windows_private_ble_irks()
    command_topic = f"{config.topic_root}/{config.observer_id}/pairing/command"
    result_topic = f"{config.topic_root}/{config.observer_id}/pairing/result"
    status_topic = f"{config.topic_root}/{config.observer_id}/pairing/status"
    progress_path = Path(config.interactive_pairing_result_path)
    completed = threading.Event()
    worker_started = threading.Event()
    outcome: dict[str, Any] = {
        "state": "waiting",
        "baseline_count": len(baseline),
    }

    client = mqtt.Client(
        callback_api_version=mqtt.CallbackAPIVersion.VERSION2,
        client_id=f"presence-new-bond-recovery-{config.observer_id}-{os.getpid()}",
        protocol=mqtt.MQTTv311,
    )
    if config.mqtt.username:
        client.username_pw_set(config.mqtt.username, config.mqtt.password)

    def publish_identity(payload: dict[str, Any]) -> None:
        session_id = str(payload["session_id"])
        public_key = str(payload["public_key"])
        deadline = min(float(payload["expires_at"]), time.time() + timeout)
        bond_confirmed = False
        record: dict[str, str] | None = None
        while time.time() < deadline and not completed.is_set():
            stage = read_progress(progress_path, session_id)
            bond_confirmed = bond_confirmed or stage in BOND_READY_STAGES
            if bond_confirmed:
                new_records = select_new_irk_records(
                    baseline,
                    read_windows_private_ble_irks(),
                )
                if len(new_records) == 1:
                    record = new_records[0]
                    break
                if len(new_records) > 1:
                    outcome.update(state="error", error="multiple_new_identities")
                    write_status(args.status, **outcome)
                    completed.set()
                    return
            time.sleep(0.1)
        if record is None:
            outcome.update(
                state="error",
                error=(
                    "new_identity_not_found"
                    if bond_confirmed
                    else "verified_bond_not_observed"
                ),
            )
            write_status(args.status, **outcome)
            completed.set()
            return

        encrypted = encrypt_pairing_result(
            public_key,
            {
                "irk": record["irk"],
                "matched_address": normalize_address(record.get("registry_leaf")),
                "captured_at": datetime.now(UTC).isoformat(),
                "claim_verified": True,
                "recovery": "qr_verified_new_windows_bond",
            },
        )
        result = {
            "schema": 2,
            "observer_id": config.observer_id,
            "session_id": session_id,
            "ciphertext": encrypted,
        }
        client.publish(
            result_topic,
            json.dumps(result, separators=(",", ":")),
            qos=1,
            retain=False,
        ).wait_for_publish(timeout=10)
        progress = {
            "schema": 2,
            "observer_id": config.observer_id,
            "session_id": session_id,
            "state": "identity_captured",
            "message": "QR-verified Windows bond recovered; Home Assistant is verifying it",
            "updated_at": datetime.now(UTC).isoformat(),
            "detail_code": "qr_verified_new_bond_recovered",
            "transport": "iphone_peripheral",
        }
        client.publish(
            status_topic,
            json.dumps(progress, separators=(",", ":")),
            qos=1,
            retain=False,
        ).wait_for_publish(timeout=10)
        outcome.update(state="published", session_id=session_id)
        write_status(args.status, **outcome)
        restart_observer(args.observer_task, args.helper_task)
        completed.set()

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
        _connected_client: mqtt.Client,
        _userdata: Any,
        message: Any,
    ) -> None:
        try:
            payload = json.loads(message.payload.decode("utf-8"))
            session_id = str(payload.get("session_id") or "")
            if payload.get("action") != "start_app_pairing":
                return
            if (
                worker_started.is_set()
                or not SESSION_ID_RE.fullmatch(session_id)
                or str(payload.get("observer_id") or "") != config.observer_id
                or int(payload.get("expires_at") or 0) <= int(time.time()) + 10
                or not payload.get("public_key")
            ):
                return
            worker_started.set()
            outcome.update(state="monitoring", session_id=session_id)
            write_status(args.status, **outcome)
            threading.Thread(
                target=publish_identity,
                args=(payload,),
                daemon=True,
            ).start()
        except (AttributeError, TypeError, ValueError, json.JSONDecodeError):
            return

    client.on_connect = on_connect
    client.on_message = on_message
    try:
        client.connect(config.mqtt.host, config.mqtt.port, keepalive=30)
        client.loop_start()
        if not completed.wait(timeout + 15):
            outcome.update(state="error", error="pairing_command_timeout")
            write_status(args.status, **outcome)
            return 4
        return 0 if outcome.get("state") == "published" else 5
    finally:
        client.disconnect()
        client.loop_stop()


if __name__ == "__main__":
    raise SystemExit(main())
