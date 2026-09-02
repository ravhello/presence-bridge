"""Exercise app-assisted GATT startup through the running Windows observer."""

from __future__ import annotations

import argparse
import base64
import json
import queue
import secrets
import sys
import threading
import time
from pathlib import Path
from typing import Any

import paho.mqtt.client as mqtt
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from protocol import b64url_encode

GATT_SERVICE_UUID = "61dd168c-4ec1-40de-a78c-ccdce5774bba"
GATT_SESSION_UUID = "ef70387a-ba9d-4e83-9171-fea99252b57a"
GATT_CLAIM_UUID = "700dbb64-64ed-4f51-adae-b106a00e908a"
GATT_RESULT_UUID = "fb5312b1-c24c-42b5-8d21-5263874c258f"


def build_start_payload(observer_id: str, *, now: int | None = None) -> dict[str, Any]:
    """Build a valid invitation without exposing it to a phone."""
    current = int(time.time()) if now is None else int(now)
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    public_der = private_key.public_key().public_bytes(
        serialization.Encoding.DER,
        serialization.PublicFormat.SubjectPublicKeyInfo,
    )
    return {
        "schema": 2,
        "action": "start_app_pairing",
        "session_id": secrets.token_urlsafe(24),
        "observer_id": observer_id,
        "expires_at": current + 90,
        "timeout_seconds": 60,
        "app_secret": b64url_encode(secrets.token_bytes(32)),
        "public_key": base64.b64encode(public_der).decode("ascii"),
        "gatt": {
            "service_uuid": GATT_SERVICE_UUID,
            "session_uuid": GATT_SESSION_UUID,
            "claim_uuid": GATT_CLAIM_UUID,
            "result_uuid": GATT_RESULT_UUID,
        },
    }


def run_smoke_test(config_path: Path, timeout: float) -> dict[str, Any]:
    """Start and cancel one private GATT invitation over MQTT."""
    raw = json.loads(config_path.read_text(encoding="utf-8-sig"))
    observer_id = str(raw["observer_id"]).strip().lower()
    mqtt_config = raw["mqtt"]
    topic_root = str(raw.get("topic_root") or "presence_bridge/v1/observers").strip("/")
    base_topic = f"{topic_root}/{observer_id}/pairing"
    command_topic = f"{base_topic}/command"
    status_topic = f"{base_topic}/status"
    states: queue.Queue[dict[str, Any]] = queue.Queue()
    subscribed = threading.Event()

    client = mqtt.Client(
        callback_api_version=mqtt.CallbackAPIVersion.VERSION2,
        client_id=f"presence-bridge-smoke-{secrets.token_hex(6)}",
        protocol=mqtt.MQTTv311,
    )
    username = str(mqtt_config.get("username") or "")
    if username:
        client.username_pw_set(username, str(mqtt_config.get("password") or ""))

    def on_connect(
        client_instance: mqtt.Client,
        userdata: Any,
        flags: Any,
        reason_code: Any,
        properties: Any,
    ) -> None:
        if bool(getattr(reason_code, "is_failure", False)):
            states.put({"state": "error", "message": f"MQTT: {reason_code}"})
            return
        client_instance.subscribe(status_topic, qos=1)

    def on_subscribe(
        client_instance: mqtt.Client,
        userdata: Any,
        mid: int,
        reason_code_list: Any,
        properties: Any,
    ) -> None:
        subscribed.set()

    def on_message(
        client_instance: mqtt.Client,
        userdata: Any,
        message: Any,
    ) -> None:
        try:
            payload = json.loads(message.payload.decode("utf-8"))
        except (AttributeError, UnicodeDecodeError, json.JSONDecodeError):
            return
        if isinstance(payload, dict):
            states.put(payload)

    client.on_connect = on_connect
    client.on_message = on_message
    client.on_subscribe = on_subscribe
    started = time.monotonic()
    payload = build_start_payload(observer_id)
    session_id = payload["session_id"]
    seen_states: list[str] = []

    def wait_for(expected: str) -> dict[str, Any]:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            try:
                status = states.get(timeout=max(0.1, deadline - time.monotonic()))
            except queue.Empty as err:
                raise TimeoutError(f"Timed out waiting for {expected}") from err
            if status.get("session_id") != session_id:
                continue
            state = str(status.get("state") or "")
            if state:
                seen_states.append(state)
            if state == "error":
                raise RuntimeError(str(status.get("message") or "Observer error"))
            if state == expected:
                return status
        raise TimeoutError(f"Timed out waiting for {expected}")

    loop_started = False
    try:
        client.connect(
            str(mqtt_config["host"]),
            int(mqtt_config.get("port", 1883)),
            keepalive=30,
        )
        client.loop_start()
        loop_started = True
        if not subscribed.wait(timeout):
            raise TimeoutError("Timed out subscribing to the observer status")
        published = client.publish(
            command_topic,
            json.dumps(payload, separators=(",", ":")),
            qos=1,
            retain=False,
        )
        published.wait_for_publish(timeout=timeout)
        wait_for("waiting_for_app")
        cancelled = client.publish(
            command_topic,
            json.dumps(
                {
                    "schema": 2,
                    "action": "cancel",
                    "session_id": session_id,
                },
                separators=(",", ":"),
            ),
            qos=1,
            retain=False,
        )
        cancelled.wait_for_publish(timeout=timeout)
        wait_for("cancelled")
        return {
            "compatible": True,
            "observer_id": observer_id,
            "states": seen_states,
            "elapsed_seconds": round(time.monotonic() - started, 2),
        }
    finally:
        try:
            if client.is_connected():
                client.publish(
                    command_topic,
                    json.dumps(
                        {"schema": 2, "action": "cancel", "session_id": session_id},
                        separators=(",", ":"),
                    ),
                    qos=1,
                    retain=False,
                ).wait_for_publish(timeout=min(timeout, 3))
        finally:
            client.disconnect()
            if loop_started:
                client.loop_stop()


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Verify the running Presence Bridge GATT pairing path."
    )
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--timeout", type=float, default=20.0)
    args = parser.parse_args()
    try:
        result = run_smoke_test(args.config, max(5.0, args.timeout))
    except Exception as exc:
        print(
            json.dumps({"compatible": False, "error": str(exc) or type(exc).__name__}),
            file=sys.stderr,
        )
        return 1
    print(json.dumps(result))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
