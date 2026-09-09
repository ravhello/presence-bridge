#!/usr/bin/env python3
"""Windows Bluetooth observer and secure pairing bridge.

The observer runs on an always-on Windows host, collects nearby BLE
advertisements and publishes a bounded RSSI snapshot to Home Assistant over
MQTT. Normal operation is passive; during app-assisted pairing the observer
temporarily becomes a BLE central and connects only to the authenticated GATT
service advertised by Presence Pair.
"""

from __future__ import annotations

import argparse
import asyncio
import base64
import binascii
import ctypes
import hmac
import json
import logging
import re
import signal
import sys
import time
from contextlib import suppress
from dataclasses import dataclass
from datetime import UTC, datetime
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Any, ClassVar

import paho.mqtt.client as mqtt
from bleak import BleakScanner
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from protocol import PairingLink, ProtocolError, b64url_decode
from reverse_gatt_client import (
    PAIRING_COMPLETION_GRACE_SECONDS,
    ReverseGattError,
    ReverseGattPairingClient,
    ReverseGattResult,
)

LOGGER = logging.getLogger("ble_presence_observer")
BRIDGE_VERSION = "0.1.20"
OBSERVER_ID_RE = re.compile(r"^[a-z0-9_]{3,64}$")
MAX_SERVICE_UUIDS = 12
MAX_MANUFACTURER_IDS = 12
DISCOVERY_REFRESH_SECONDS = 60.0
PAIRING_SESSION_ID_RE = re.compile(r"^[A-Za-z0-9_-]{16,96}$")
PRIVATE_BLE_REGISTRY_PATH = r"SYSTEM\CurrentControlSet\Services\BTHPORT\Parameters\Keys"
MAX_PAIRING_TIMEOUT_SECONDS = 600
MIN_PAIRING_TIMEOUT_SECONDS = 60
SCANNER_PAUSE_TIMEOUT_SECONDS = 10.0
PAIRING_HEARTBEAT_SECONDS = 10.0
APP_PAIRING_TRANSPORT = "iphone_peripheral"
PAIRING_ACK_RECONNECT_GRACE_SECONDS = 30.0


def mqtt_reason_is_failure(reason_code: Any) -> bool:
    """Support Paho v2 reason codes and legacy integer codes."""
    is_failure = getattr(reason_code, "is_failure", None)
    if is_failure is not None:
        return bool(is_failure)
    try:
        return int(reason_code) != 0
    except (TypeError, ValueError):
        return True


def normalize_address(value: Any) -> str:
    """Normalize a Bluetooth MAC-like address without inventing identities."""
    compact = "".join(
        character
        for character in str(value or "").upper()
        if character in "0123456789ABCDEF"
    )
    if len(compact) != 12:
        return ""
    return ":".join(compact[index : index + 2] for index in range(0, 12, 2))


def scan_session_is_stale(
    session_started: float,
    last_detection: float,
    now: float,
    timeout: float,
) -> bool:
    """Return whether a live scanner stopped delivering advertisements."""
    return now - max(session_started, last_detection) >= timeout


def pairing_ack_fallback_ready(first_seen: float | None, now: float) -> bool:
    """Allow IRK-only completion only after the iPhone ACK had time to reconnect."""
    return (
        first_seen is not None
        and now - first_seen >= PAIRING_ACK_RECONNECT_GRACE_SECONDS
    )


def read_windows_private_ble_irks() -> list[dict[str, str]]:
    """Read Windows Bluetooth IRKs while running as LOCAL SYSTEM."""
    if sys.platform != "win32":
        return []
    import winreg

    records: list[dict[str, str]] = []

    def visit(key: Any, relative_path: str, depth: int = 0) -> None:
        if depth > 4:
            return
        value_index = 0
        while True:
            try:
                value_name, value, _value_type = winreg.EnumValue(key, value_index)
            except OSError:
                break
            value_index += 1
            normalized_name = value_name.casefold()
            # CentralIRK belongs to the local Windows adapter. It cannot
            # resolve a phone address and must never be exported as a peer.
            if normalized_name == "centralirk" or "irk" not in normalized_name:
                continue
            if not isinstance(value, bytes) or len(value) != 16:
                continue
            records.append(
                {
                    "registry_path": relative_path,
                    "registry_leaf": relative_path.rsplit("\\", 1)[-1],
                    "value_name": value_name,
                    "irk": value.hex().upper(),
                }
            )

        child_index = 0
        while True:
            try:
                child_name = winreg.EnumKey(key, child_index)
            except OSError:
                break
            child_index += 1
            try:
                with winreg.OpenKey(key, child_name, 0, winreg.KEY_READ) as child:
                    visit(child, f"{relative_path}\\{child_name}", depth + 1)
            except OSError:
                continue

    try:
        with winreg.OpenKey(
            winreg.HKEY_LOCAL_MACHINE,
            PRIVATE_BLE_REGISTRY_PATH,
            0,
            winreg.KEY_READ | getattr(winreg, "KEY_WOW64_64KEY", 0),
        ) as root:
            visit(root, PRIVATE_BLE_REGISTRY_PATH)
    except OSError:
        return []
    return records


def select_new_irk_records(
    baseline: list[dict[str, str]], current: list[dict[str, str]]
) -> list[dict[str, str]]:
    """Return one row per IRK that appeared after a pairing session began."""
    known = {str(row.get("irk") or "").upper() for row in baseline}
    selected: dict[str, dict[str, str]] = {}
    for row in current:
        irk = str(row.get("irk") or "").upper()
        if re.fullmatch(r"[0-9A-F]{32}", irk) and irk not in known:
            selected.setdefault(irk, row)
    return list(selected.values())


def select_irk_record_for_address(
    records: list[dict[str, str]], address: str
) -> dict[str, str] | None:
    """Select the unique Windows IRK matching an identity address or BLE RPA."""
    normalized = normalize_address(address)
    if not normalized:
        return None
    direct = [
        row
        for row in records
        if normalize_address(row.get("registry_leaf")) == normalized
    ]
    direct_by_irk = {
        str(row.get("irk") or "").upper(): row
        for row in direct
        if re.fullmatch(r"[0-9A-F]{32}", str(row.get("irk") or "").upper())
    }
    if len(direct_by_irk) == 1:
        return next(iter(direct_by_irk.values()))

    raw_address = binascii.unhexlify(normalized.replace(":", ""))
    if raw_address[0] & 0xC0 != 0x40:
        return None
    matches: dict[str, dict[str, str]] = {}
    for row in records:
        irk = str(row.get("irk") or "").upper()
        if not re.fullmatch(r"[0-9A-F]{32}", irk):
            continue
        cipher = Cipher(algorithms.AES(bytes.fromhex(irk)), modes.ECB())
        encryptor = cipher.encryptor()
        ciphertext = (
            encryptor.update(b"\x00" * 13 + raw_address[:3])
            + encryptor.finalize()
        )
        if hmac.compare_digest(ciphertext[13:], raw_address[3:]):
            matches.setdefault(irk, row)
    return next(iter(matches.values())) if len(matches) == 1 else None


def set_bluetooth_discoverable(enabled: bool) -> bool:
    """Enable or disable discoverability on every local Windows radio."""
    if sys.platform != "win32":
        return False
    from ctypes import wintypes

    class BluetoothFindRadioParams(ctypes.Structure):
        _fields_: ClassVar = [("dwSize", wintypes.DWORD)]

    bthprops = ctypes.WinDLL("bthprops.cpl")
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    bthprops.BluetoothFindFirstRadio.argtypes = [
        ctypes.POINTER(BluetoothFindRadioParams),
        ctypes.POINTER(wintypes.HANDLE),
    ]
    bthprops.BluetoothFindFirstRadio.restype = wintypes.HANDLE
    bthprops.BluetoothFindNextRadio.argtypes = [
        wintypes.HANDLE,
        ctypes.POINTER(wintypes.HANDLE),
    ]
    bthprops.BluetoothFindNextRadio.restype = wintypes.BOOL
    bthprops.BluetoothFindRadioClose.argtypes = [wintypes.HANDLE]
    bthprops.BluetoothFindRadioClose.restype = wintypes.BOOL
    bthprops.BluetoothEnableDiscovery.argtypes = [wintypes.HANDLE, wintypes.BOOL]
    bthprops.BluetoothEnableDiscovery.restype = wintypes.BOOL
    bthprops.BluetoothEnableIncomingConnections.argtypes = [
        wintypes.HANDLE,
        wintypes.BOOL,
    ]
    bthprops.BluetoothEnableIncomingConnections.restype = wintypes.BOOL
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel32.CloseHandle.restype = wintypes.BOOL

    params = BluetoothFindRadioParams(ctypes.sizeof(BluetoothFindRadioParams))
    radio = wintypes.HANDLE()
    find_handle = bthprops.BluetoothFindFirstRadio(
        ctypes.byref(params), ctypes.byref(radio)
    )
    if not find_handle:
        return False
    changed = False
    try:
        while radio.value:
            if enabled:
                bthprops.BluetoothEnableIncomingConnections(radio, True)
            changed = bool(bthprops.BluetoothEnableDiscovery(radio, enabled)) or changed
            kernel32.CloseHandle(radio)
            radio = wintypes.HANDLE()
            if not bthprops.BluetoothFindNextRadio(find_handle, ctypes.byref(radio)):
                break
    finally:
        if radio.value:
            kernel32.CloseHandle(radio)
        bthprops.BluetoothFindRadioClose(find_handle)
    return changed


def encrypt_pairing_result(public_key_b64: str, payload: dict[str, Any]) -> str:
    """Encrypt a short pairing result for the in-memory HA session key."""
    public_key = serialization.load_der_public_key(
        base64.b64decode(public_key_b64, validate=True)
    )
    plaintext = json.dumps(payload, separators=(",", ":")).encode("utf-8")
    max_plaintext = public_key.key_size // 8 - 2 * hashes.SHA256.digest_size - 2
    if len(plaintext) > max_plaintext:
        raise ValueError(
            "Pairing result is too large for RSA-OAEP "
            f"({len(plaintext)} bytes, maximum {max_plaintext})"
        )
    ciphertext = public_key.encrypt(
        plaintext,
        padding.OAEP(
            mgf=padding.MGF1(algorithm=hashes.SHA256()),
            algorithm=hashes.SHA256(),
            label=None,
        ),
    )
    return base64.b64encode(ciphertext).decode("ascii")


def verified_app_identity_payload(record: dict[str, str]) -> dict[str, Any]:
    """Return only the authenticated identity fields consumed by Home Assistant."""
    irk = str(record.get("irk") or "").upper()
    if not re.fullmatch(r"[0-9A-F]{32}", irk):
        raise ValueError("The paired iPhone IRK is invalid")
    return {"irk": irk, "claim_verified": True}


@dataclass(frozen=True)
class MqttConfig:
    host: str
    port: int
    username: str
    password: str


@dataclass(frozen=True)
class ObserverConfig:
    observer_id: str
    name: str
    mqtt: MqttConfig
    publish_interval: float = 12.0
    observation_ttl: float = 55.0
    retry_interval: float = 10.0
    scanner_restart_interval: float = 1800.0
    scanner_stale_timeout: float = 120.0
    max_observations: int = 100
    log_path: str = "ble_presence_observer.log"
    topic_root: str = "presence_bridge/v1/observers"
    legacy_topic_root: str = "smart_presence/ble"
    app_pairing_enabled: bool = True
    interactive_pairing_task: str = ""
    interactive_pairing_command_path: str = ""
    interactive_pairing_result_path: str = ""

    @classmethod
    def load(cls, path: Path) -> ObserverConfig:
        raw = json.loads(path.read_text(encoding="utf-8-sig"))
        mqtt_raw = raw["mqtt"]
        config = cls(
            observer_id=str(raw["observer_id"]).strip().lower(),
            name=str(raw.get("name") or raw["observer_id"]).strip(),
            mqtt=MqttConfig(
                host=str(mqtt_raw["host"]).strip(),
                port=int(mqtt_raw.get("port", 1883)),
                username=str(mqtt_raw.get("username") or ""),
                password=str(mqtt_raw.get("password") or ""),
            ),
            publish_interval=max(5.0, float(raw.get("publish_interval", 12.0))),
            observation_ttl=max(20.0, float(raw.get("observation_ttl", 55.0))),
            retry_interval=max(3.0, float(raw.get("retry_interval", 10.0))),
            scanner_restart_interval=max(
                300.0,
                float(raw.get("scanner_restart_interval", 1800.0)),
            ),
            scanner_stale_timeout=max(
                60.0,
                float(raw.get("scanner_stale_timeout", 120.0)),
            ),
            max_observations=min(200, max(10, int(raw.get("max_observations", 100)))),
            log_path=str(raw.get("log_path") or path.with_suffix(".log")),
            topic_root=str(raw.get("topic_root") or "presence_bridge/v1/observers")
            .strip()
            .strip("/"),
            legacy_topic_root=str(raw.get("legacy_topic_root") or "smart_presence/ble")
            .strip()
            .strip("/"),
            app_pairing_enabled=bool(raw.get("app_pairing_enabled", True)),
            interactive_pairing_task=str(
                raw.get("interactive_pairing_task") or ""
            ).strip(),
            interactive_pairing_command_path=str(
                raw.get("interactive_pairing_command_path")
                or path.parent / "interactive-pairing-command.json"
            ),
            interactive_pairing_result_path=str(
                raw.get("interactive_pairing_result_path")
                or path.parent / "interactive-pairing-result.json"
            ),
        )
        if not OBSERVER_ID_RE.fullmatch(config.observer_id):
            raise ValueError(
                "observer_id must contain only lowercase letters, digits and underscores"
            )
        if not config.name or not config.mqtt.host:
            raise ValueError("observer name and MQTT host are required")
        if not config.topic_root:
            raise ValueError("topic_root is required")
        return config


class MqttPublisher:
    """Publish observer health, discovery and BLE snapshots."""

    def __init__(self, config: ObserverConfig) -> None:
        self.config = config
        self.base = f"{config.legacy_topic_root}/{config.observer_id}"
        self.bridge_base = f"{config.topic_root}/{config.observer_id}"
        self.connected = False
        self.client = mqtt.Client(
            callback_api_version=mqtt.CallbackAPIVersion.VERSION2,
            client_id=f"ble-presence-{config.observer_id}",
            protocol=mqtt.MQTTv311,
        )
        if config.mqtt.username:
            self.client.username_pw_set(
                config.mqtt.username,
                config.mqtt.password,
            )
        self.client.will_set(
            f"{self.base}/availability",
            "offline",
            qos=1,
            retain=True,
        )
        self.client.on_connect = self._on_connect
        self.client.on_disconnect = self._on_disconnect
        self.client.on_message = self._on_message
        self.command_handler: Any = None

    def set_command_handler(self, handler: Any) -> None:
        self.command_handler = handler

    def start(self) -> None:
        self.client.connect_async(
            self.config.mqtt.host,
            self.config.mqtt.port,
            keepalive=30,
        )
        self.client.loop_start()

    def stop(self) -> None:
        try:
            self.publish_bridge_json("status", self.status_payload(online=False))
            self.publish("availability", "offline")
            self.client.disconnect()
        finally:
            self.client.loop_stop()

    def publish(self, suffix: str, value: str, *, retain: bool = True) -> None:
        self.client.publish(
            f"{self.base}/{suffix}",
            value,
            qos=1,
            retain=retain,
        )

    def publish_json(
        self,
        topic: str,
        payload: dict[str, Any],
        *,
        retain: bool = True,
    ) -> None:
        self.client.publish(
            topic,
            json.dumps(payload, separators=(",", ":")),
            qos=1,
            retain=retain,
        )

    def publish_bridge(self, suffix: str, value: str, *, retain: bool = True) -> None:
        """Publish a scalar on the public Presence Bridge topic."""
        self.client.publish(
            f"{self.bridge_base}/{suffix}",
            value,
            qos=1,
            retain=retain,
        )

    def publish_bridge_json(
        self,
        suffix: str,
        payload: dict[str, Any],
        *,
        retain: bool = True,
    ) -> None:
        """Publish JSON on the public Presence Bridge topic."""
        self.publish_json(
            f"{self.bridge_base}/{suffix}",
            payload,
            retain=retain,
        )

    def refresh_discovery(self) -> None:
        if self.connected:
            self._publish_discovery()

    def _on_connect(
        self,
        client: mqtt.Client,
        userdata: Any,
        flags: Any,
        reason_code: Any,
        properties: Any,
    ) -> None:
        if mqtt_reason_is_failure(reason_code):
            LOGGER.error("MQTT connection rejected: %s", reason_code)
            return
        self.connected = True
        client.subscribe(f"{self.base}/pairing/command", qos=1)
        client.subscribe(f"{self.bridge_base}/pairing/command", qos=1)
        self._publish_discovery()
        self.publish("availability", "online")
        self.publish_bridge_json("status", self.status_payload(online=True))
        LOGGER.info("MQTT connected")

    def _on_disconnect(
        self,
        client: mqtt.Client,
        userdata: Any,
        disconnect_flags: Any,
        reason_code: Any,
        properties: Any,
    ) -> None:
        self.connected = False
        LOGGER.warning("MQTT disconnected: %s", reason_code)

    def _on_message(
        self,
        client: mqtt.Client,
        userdata: Any,
        message: Any,
    ) -> None:
        if message.topic not in {
            f"{self.base}/pairing/command",
            f"{self.bridge_base}/pairing/command",
        }:
            return
        try:
            payload = json.loads(message.payload.decode("utf-8"))
        except (AttributeError, UnicodeDecodeError, json.JSONDecodeError):
            return
        if isinstance(payload, dict) and self.command_handler is not None:
            self.command_handler(payload)

    def status_payload(self, *, online: bool) -> dict[str, Any]:
        """Describe this bridge without exposing local credentials."""
        capabilities = ["scanner", "manual_pairing"]
        if self.config.app_pairing_enabled:
            capabilities.append("app_pairing")
        return {
            "schema": 1,
            "observer_id": self.config.observer_id,
            "name": self.config.name,
            "online": online,
            "timestamp": datetime.now(UTC).isoformat(),
            "version": BRIDGE_VERSION,
            "platform": "windows",
            "capabilities": capabilities,
        }

    def _publish_discovery(self) -> None:
        observer_id = self.config.observer_id
        device = {
            "identifiers": [f"ble_presence_observer_{observer_id}"],
            "name": self.config.name,
            "manufacturer": "Dell / Home Assistant local",
            "model": "Bluetooth observer",
        }
        common = {
            "device": device,
            "availability_topic": f"{self.base}/availability",
            "payload_available": "online",
            "payload_not_available": "offline",
            "has_entity_name": True,
        }
        discovery = {
            f"homeassistant/binary_sensor/ble_presence_{observer_id}_online/config": {
                "device": device,
                "name": "Online",
                "unique_id": f"ble_presence_{observer_id}_online",
                "default_entity_id": f"binary_sensor.ricevitore_bluetooth_{observer_id}_online",
                "device_class": "connectivity",
                "state_topic": f"{self.base}/availability",
                "payload_on": "online",
                "payload_off": "offline",
                "entity_category": "diagnostic",
                "has_entity_name": True,
            },
            f"homeassistant/sensor/ble_presence_{observer_id}_count/config": {
                **common,
                "name": "Dispositivi rilevati",
                "unique_id": f"ble_presence_{observer_id}_count",
                "default_entity_id": f"sensor.ricevitore_bluetooth_{observer_id}_dispositivi",
                "icon": "mdi:bluetooth-audio",
                "state_topic": f"{self.base}/device_count",
                "state_class": "measurement",
            },
            f"homeassistant/sensor/ble_presence_{observer_id}_last_scan/config": {
                **common,
                "name": "Ultima scansione",
                "unique_id": f"ble_presence_{observer_id}_last_scan",
                "default_entity_id": f"sensor.ricevitore_bluetooth_{observer_id}_ultimo_scan",
                "device_class": "timestamp",
                "state_topic": f"{self.base}/last_scan",
                "entity_category": "diagnostic",
            },
            f"homeassistant/sensor/ble_presence_{observer_id}_error/config": {
                **common,
                "name": "Diagnostica",
                "unique_id": f"ble_presence_{observer_id}_error",
                "default_entity_id": f"sensor.ricevitore_bluetooth_{observer_id}_diagnostica",
                "icon": "mdi:bluetooth-connect",
                "state_topic": f"{self.base}/error",
                "entity_category": "diagnostic",
            },
        }
        for topic, payload in discovery.items():
            self.publish_json(topic, payload)


class BlePresenceObserver:
    """Maintain a passive BLE scan and publish fresh bounded observations."""

    def __init__(self, config: ObserverConfig) -> None:
        self.config = config
        self.mqtt = MqttPublisher(config)
        self.stop_event = asyncio.Event()
        self.observations: dict[str, dict[str, Any]] = {}
        self._last_discovery_refresh_monotonic = 0.0
        self._last_detection_monotonic = 0.0
        self._loop: asyncio.AbstractEventLoop | None = None
        self._pairing_commands: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
        self._active_pairing_task: asyncio.Task[Any] | None = None
        self._active_pairing_action: str | None = None
        self._active_pairing_session_id: str | None = None
        self._scanner_pause_requested = asyncio.Event()
        self._scanner_stopped = asyncio.Event()
        self._scanner_stopped.set()
        self.mqtt.set_command_handler(self._receive_pairing_command)

    async def _pause_scanner_for_pairing(self) -> None:
        """Release the Bluetooth adapter before starting a pairing host."""
        self._scanner_pause_requested.set()
        try:
            await asyncio.wait_for(
                self._scanner_stopped.wait(),
                timeout=SCANNER_PAUSE_TIMEOUT_SECONDS,
            )
        except TimeoutError:
            self._scanner_pause_requested.clear()
            raise RuntimeError(
                "The Bluetooth presence scan did not stop before pairing"
            ) from None
        except asyncio.CancelledError:
            self._scanner_pause_requested.clear()
            raise
        LOGGER.info("Bluetooth presence scan paused for pairing")

    def _resume_scanner_after_pairing(self) -> None:
        """Allow passive presence scanning to resume after pairing."""
        if self._scanner_pause_requested.is_set():
            self._scanner_pause_requested.clear()
            LOGGER.info("Bluetooth presence scan resuming after pairing")

    def _receive_pairing_command(self, payload: dict[str, Any]) -> None:
        if self._loop is not None:
            self._loop.call_soon_threadsafe(
                self._pairing_commands.put_nowait,
                payload,
            )

    def _publish_pairing_status(
        self,
        session_id: str,
        state: str,
        message: str,
        **extra: Any,
    ) -> None:
        payload = {
            "schema": 2,
            "observer_id": self.config.observer_id,
            "session_id": session_id,
            "state": state,
            "message": message[:240],
            "updated_at": datetime.now(UTC).isoformat(),
            **extra,
        }
        self.mqtt.publish_bridge_json("pairing/status", payload)
        self.mqtt.publish_json(f"{self.mqtt.base}/pairing/status", payload)

    def _publish_observer_heartbeat(self) -> None:
        """Keep HA aware that the receiver is alive while scanning is paused."""
        self.mqtt.publish_bridge_json(
            "status",
            self.mqtt.status_payload(online=True),
        )
        self.mqtt.publish("availability", "online")

    async def _pairing_heartbeat_loop(
        self,
        session_id: str,
        progress: dict[str, Any],
    ) -> None:
        """Republish pairing progress until the GATT session finishes."""
        while not self.stop_event.is_set():
            self._publish_observer_heartbeat()
            self._publish_pairing_status(
                session_id,
                str(progress["state"]),
                str(progress["message"]),
                **dict(progress.get("extra") or {}),
            )
            await self._sleep_or_stop(PAIRING_HEARTBEAT_SECONDS)

    async def _pairing_command_loop(self) -> None:
        while not self.stop_event.is_set():
            payload = await self._pairing_commands.get()
            action = str(payload.get("action") or "").strip().lower()
            session_id = str(payload.get("session_id") or "").strip()
            if action == "cancel":
                if (
                    self._active_pairing_task is not None
                    and not self._active_pairing_task.done()
                    and (
                        not session_id
                        or session_id == self._active_pairing_session_id
                    )
                ):
                    self._active_pairing_task.cancel()
                continue
            if action not in {
                "start",
                "start_app_pairing",
            } or not PAIRING_SESSION_ID_RE.fullmatch(session_id):
                continue
            public_key = str(payload.get("public_key") or "").strip()
            try:
                timeout = int(payload.get("timeout_seconds") or 300)
                timeout = min(
                    MAX_PAIRING_TIMEOUT_SECONDS,
                    max(MIN_PAIRING_TIMEOUT_SECONDS, timeout),
                )
                serialization.load_der_public_key(
                    base64.b64decode(public_key, validate=True)
                )
                if action == "start_app_pairing":
                    if not self.config.app_pairing_enabled:
                        raise ValueError("App pairing is disabled")
                    expires_at = int(payload.get("expires_at"))
                    app_secret = b64url_decode(str(payload.get("app_secret") or ""))
                    gatt = payload.get("gatt")
                    if not isinstance(gatt, dict):
                        raise ValueError("Missing GATT configuration")
                    link = PairingLink(
                        session_id=session_id,
                        observer_id=self.config.observer_id,
                        expires_at=expires_at,
                        secret=app_secret,
                    )
                    link.validate()
            except (TypeError, ValueError, ProtocolError):
                self._publish_pairing_status(
                    session_id,
                    "error",
                    "Invalid pairing command",
                )
                continue
            if (
                self._active_pairing_task is not None
                and not self._active_pairing_task.done()
                and action == self._active_pairing_action
                and session_id == self._active_pairing_session_id
            ):
                LOGGER.info(
                    "Ignoring duplicate %s command for the active pairing session",
                    action,
                )
                continue
            if self._active_pairing_task is not None:
                self._active_pairing_task.cancel()
                with suppress(asyncio.CancelledError):
                    await self._active_pairing_task
            self._active_pairing_action = action
            self._active_pairing_session_id = session_id
            if action == "start_app_pairing":
                self._active_pairing_task = asyncio.create_task(
                    self._run_app_pairing_session(
                        link,
                        public_key,
                        timeout,
                        gatt,
                    )
                )
            else:
                self._active_pairing_task = asyncio.create_task(
                    self._run_pairing_session(session_id, public_key, timeout)
                )

    async def _run_app_pairing_session(
        self,
        link: PairingLink,
        public_key: str,
        timeout_seconds: int,
        gatt: dict[str, Any],
    ) -> None:
        """Run the GATT role supported by the installed Presence Pair build."""
        scanner_paused = False
        client: ReverseGattPairingClient | None = None
        heartbeat_task: asyncio.Task[None] | None = None
        pairing_transport = APP_PAIRING_TRANSPORT
        progress: dict[str, Any] = {
            "state": "waiting_for_app",
            "message": "Receiver ready; scan the QR code and keep Presence Pair open",
            "extra": {
                "expires_at": link.expires_at,
                "detail_code": "waiting_for_iphone_advertisement",
                "transport": pairing_transport,
            },
        }
        try:
            await self._pause_scanner_for_pairing()
            scanner_paused = True
            baseline = await asyncio.to_thread(read_windows_private_ble_irks)

            progress_states = {
                "waiting_for_iphone_advertisement": "waiting_for_app",
                "iphone_advertisement_seen": "connecting",
                "iphone_candidate_unverified": "connecting",
                "iphone_connected": "verifying",
                "iphone_session_verified": "bonding",
                "iphone_bond_ready": "bonding",
                "iphone_bond_settling": "bonding",
                "iphone_claim_received": "bonding",
                "iphone_claim_rejected": "waiting_for_app",
                "iphone_claim_accepted": "bonding",
                "iphone_ack_deferred": "bonding",
                "iphone_session_mismatch": "waiting_for_app",
                "iphone_connection_failed": "connecting",
                "iphone_connection_retry": "connecting",
                "iphone_signal_too_weak": "connecting",
                "iphone_bond_reset": "bonding",
                "existing_bond_resolved": "bonding",
                "legacy_receiver_advertising": "waiting_for_app",
                "windows_adapter_recovering": "waiting_for_app",
            }

            def publish_progress(
                detail_code: str,
                message: str,
                **reported_lease: Any,
            ) -> None:
                lease = dict(reported_lease)
                if client is not None:
                    lease.update(client.lease_payload)
                public_detail_code = (
                    "iphone_candidate_unverified"
                    if detail_code
                    in {"iphone_advertisement_seen", "iphone_connected"}
                    else detail_code
                )
                progress.update(
                    {
                        "state": progress_states.get(
                            public_detail_code,
                            "waiting_for_app",
                        ),
                        "message": message,
                        "extra": {
                            "expires_at": link.expires_at,
                            "detail_code": public_detail_code,
                            "transport": pairing_transport,
                            **lease,
                        },
                    }
                )
                self._publish_pairing_status(
                    link.session_id,
                    str(progress["state"]),
                    message,
                    **dict(progress["extra"]),
                )

            publish_progress(
                "waiting_for_iphone_advertisement",
                "Receiver ready; scan the QR code and keep Presence Pair open",
            )
            heartbeat_task = asyncio.create_task(
                self._pairing_heartbeat_loop(link.session_id, progress)
            )
            if self.config.interactive_pairing_task:
                peer = await self._run_interactive_app_pairing(
                    link,
                    timeout_seconds,
                    gatt,
                    publish_progress,
                )
            else:
                client = ReverseGattPairingClient(
                    service_uuid=str(gatt["service_uuid"]),
                    session_uuid=str(gatt["session_uuid"]),
                    claim_uuid=str(gatt["claim_uuid"]),
                    result_uuid=str(gatt["result_uuid"]),
                    progress_callback=publish_progress,
                )
                peer = await client.async_pair(link, timeout_seconds)
            reused_bond = peer.transport == "existing_windows_bond"
            self._publish_pairing_status(
                link.session_id,
                "bonding",
                (
                    "Existing Windows bond recognized; capturing the private identity"
                    if reused_bond
                    else "Encrypted claim accepted; capturing the private identity"
                ),
                detail_code=(
                    "existing_bond_resolved"
                    if reused_bond
                    else "iphone_claim_accepted"
                ),
                transport=peer.transport,
            )
            deadline = time.monotonic() + min(30, timeout_seconds)
            while time.monotonic() < deadline:
                current = await asyncio.to_thread(read_windows_private_ble_irks)
                new_records = select_new_irk_records(baseline, current)
                record = (
                    new_records[0]
                    if len(new_records) == 1
                    else select_irk_record_for_address(
                        new_records or current,
                        peer.address,
                    )
                )
                if record is not None:
                    encrypted = encrypt_pairing_result(
                        public_key,
                        verified_app_identity_payload(record),
                    )
                    result_payload = {
                        "schema": 2,
                        "observer_id": self.config.observer_id,
                        "session_id": link.session_id,
                        "ciphertext": encrypted,
                    }
                    self.mqtt.publish_bridge_json(
                        "pairing/result",
                        result_payload,
                        retain=False,
                    )
                    self.mqtt.publish_json(
                        f"{self.mqtt.base}/pairing/result",
                        result_payload,
                        retain=False,
                    )
                    self._publish_pairing_status(
                        link.session_id,
                        "identity_captured",
                        "Identity captured; Home Assistant is verifying it",
                        detail_code="identity_captured",
                        transport=peer.transport,
                    )
                    return
                if len(new_records) > 1:
                    raise RuntimeError("More than one new Bluetooth identity appeared")
                await asyncio.sleep(1)
            raise RuntimeError(
                "The secure bond completed, but its Windows IRK could not be identified"
            )
        except ReverseGattError as exc:
            self._publish_pairing_status(
                link.session_id,
                "timeout",
                str(exc),
                detail_code=exc.detail_code,
                transport=pairing_transport,
            )
        except asyncio.CancelledError:
            self._publish_pairing_status(
                link.session_id,
                "cancelled",
                "Pairing cancelled",
            )
            raise
        except Exception as exc:
            LOGGER.exception("App-assisted Bluetooth pairing failed")
            self._publish_pairing_status(
                link.session_id,
                "error",
                str(exc) or type(exc).__name__,
                detail_code=getattr(client, "detail_code", None) or "pairing_failed",
                transport=pairing_transport,
            )
        finally:
            if heartbeat_task is not None:
                heartbeat_task.cancel()
                with suppress(asyncio.CancelledError):
                    await heartbeat_task
            if scanner_paused:
                self._resume_scanner_after_pairing()

    async def _run_interactive_app_pairing(
        self,
        link: PairingLink,
        timeout_seconds: int,
        gatt: dict[str, Any],
        progress_callback: Any,
    ) -> ReverseGattResult:
        """Delegate WinRT GATT to the logged-in user's Bluetooth session."""
        command_path = Path(self.config.interactive_pairing_command_path)
        result_path = Path(self.config.interactive_pairing_result_path)
        command_path.parent.mkdir(parents=True, exist_ok=True)
        result_path.parent.mkdir(parents=True, exist_ok=True)
        command_path.unlink(missing_ok=True)
        result_path.unlink(missing_ok=True)
        temporary = command_path.with_suffix(command_path.suffix + ".tmp")
        temporary.write_text(
            json.dumps(
                {
                    "schema": 1,
                    "session_id": link.session_id,
                    "pairing_uri": link.to_uri(),
                    "timeout_seconds": timeout_seconds,
                    "transport": APP_PAIRING_TRANSPORT,
                    "gatt": gatt,
                },
                separators=(",", ":"),
                sort_keys=True,
            ),
            encoding="utf-8",
        )
        temporary.replace(command_path)

        async def task_command(action: str, *, required: bool) -> None:
            process = await asyncio.create_subprocess_exec(
                "schtasks.exe",
                action,
                "/TN",
                self.config.interactive_pairing_task,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=15)
            if required and process.returncode != 0:
                detail = (stderr or stdout).decode(errors="replace").strip()
                raise ReverseGattError(
                    f"The interactive Windows Bluetooth helper did not start: {detail}",
                    "interactive_receiver_unavailable",
                )

        await task_command("/End", required=False)
        await task_command("/Run", required=True)
        deadline = min(
            time.monotonic() + timeout_seconds,
            time.monotonic() + max(1, link.expires_at - int(time.time())),
        )
        last_update: tuple[str, str] | None = None
        completion_started = False
        existing_bond_seen_at: float | None = None
        try:
            while True:
                if result_path.is_file():
                    try:
                        payload = json.loads(
                            result_path.read_text(encoding="utf-8-sig")
                        )
                    except (OSError, json.JSONDecodeError):
                        await asyncio.sleep(0.25)
                        continue
                    if payload.get("session_id") != link.session_id:
                        await asyncio.sleep(0.25)
                        continue
                    state = str(payload.get("state") or "")
                    detail_code = str(
                        payload.get("detail_code") or "interactive_pairing"
                    )
                    message = str(payload.get("message") or detail_code)
                    matched_address = normalize_address(
                        str(payload.get("matched_address") or "")
                    )
                    session_scoped_advertisement = (
                        payload.get("session_scoped_advertisement") is True
                    )
                    reported_lease: dict[str, int] = {}
                    try:
                        reported_rssi = int(payload.get("rssi"))
                    except (TypeError, ValueError):
                        reported_rssi = None
                    if reported_rssi is not None and -127 <= reported_rssi <= 20:
                        reported_lease["rssi"] = reported_rssi
                    now_epoch = int(time.time())
                    now_monotonic = time.monotonic()
                    try:
                        attempt_expires_at = int(payload.get("attempt_expires_at") or 0)
                    except (TypeError, ValueError):
                        attempt_expires_at = 0
                    try:
                        completion_expires_at = int(
                            payload.get("completion_expires_at") or 0
                        )
                    except (TypeError, ValueError):
                        completion_expires_at = 0
                    if completion_expires_at > now_epoch:
                        deadline = now_monotonic + completion_expires_at - now_epoch
                        completion_started = True
                        reported_lease["completion_expires_at"] = completion_expires_at
                    elif attempt_expires_at > now_epoch and not completion_started:
                        deadline = now_monotonic + attempt_expires_at - now_epoch
                        reported_lease["attempt_expires_at"] = attempt_expires_at
                    elif (
                        detail_code
                        in {
                            "iphone_session_verified",
                            "iphone_bond_ready",
                            "iphone_bond_settling",
                            "iphone_bond_reconnecting",
                            "iphone_claim_received",
                            "iphone_claim_accepted",
                            "iphone_ack_deferred",
                        }
                        and not completion_started
                    ):
                        deadline = now_monotonic + PAIRING_COMPLETION_GRACE_SECONDS
                        completion_started = True
                        reported_lease["completion_expires_at"] = int(
                            time.time() + PAIRING_COMPLETION_GRACE_SECONDS
                        )
                    update = (detail_code, message)
                    if state == "progress" and update != last_update:
                        last_update = update
                        progress_callback(detail_code, message, **reported_lease)
                    if (
                        state == "progress"
                        and detail_code
                        in {
                            "iphone_connection_retry",
                            "iphone_connection_failed",
                            "iphone_signal_too_weak",
                        }
                        and session_scoped_advertisement
                        and matched_address
                    ):
                        record = select_irk_record_for_address(
                            await asyncio.to_thread(read_windows_private_ble_irks),
                            matched_address,
                        )
                        if record is not None:
                            if existing_bond_seen_at is None:
                                existing_bond_seen_at = now_monotonic
                                progress_callback(
                                    "iphone_bond_settling",
                                    "Bluetooth bond accepted; waiting for the iPhone confirmation channel",
                                )
                            elif pairing_ack_fallback_ready(
                                existing_bond_seen_at,
                                now_monotonic,
                            ):
                                progress_callback(
                                    "existing_bond_resolved",
                                    "The QR-matched iPhone already has a valid Windows bond; reusing it",
                                )
                                return ReverseGattResult(
                                    address=matched_address,
                                    name=str(
                                        payload.get("name")
                                        or "Presence Pair iPhone"
                                    ),
                                    transport="existing_windows_bond",
                                )
                    elif state == "success":
                        return ReverseGattResult(
                            address=str(payload.get("address") or ""),
                            name=str(payload.get("name") or "Presence Pair iPhone"),
                            transport=str(
                                payload.get("transport") or APP_PAIRING_TRANSPORT
                            ),
                        )
                    elif state == "error":
                        raise ReverseGattError(message, detail_code)
                if time.monotonic() >= deadline:
                    break
                await asyncio.sleep(0.35)
            raise ReverseGattError(
                "The logged-in Windows Bluetooth session timed out",
                "interactive_receiver_timeout",
            )
        finally:
            await task_command("/End", required=False)
            command_path.unlink(missing_ok=True)

    async def _run_pairing_session(
        self,
        session_id: str,
        public_key: str,
        timeout_seconds: int,
    ) -> None:
        scanner_paused = False
        try:
            await self._pause_scanner_for_pairing()
            scanner_paused = True
            baseline = await asyncio.to_thread(read_windows_private_ble_irks)
            discoverable = await asyncio.to_thread(set_bluetooth_discoverable, True)
            if not discoverable:
                raise RuntimeError("Nessun adattatore Bluetooth reso rilevabile")
            expires_at = datetime.fromtimestamp(
                time.time() + timeout_seconds,
                tz=UTC,
            ).isoformat()
            self._publish_pairing_status(
                session_id,
                "waiting_for_phone",
                "Dell rilevabile: selezionalo dal telefono e conferma il codice",
                expires_at=expires_at,
            )
            deadline = time.monotonic() + timeout_seconds
            while time.monotonic() < deadline:
                current = await asyncio.to_thread(read_windows_private_ble_irks)
                new_records = select_new_irk_records(baseline, current)
                if len(new_records) == 1:
                    record = new_records[0]
                    encrypted = encrypt_pairing_result(
                        public_key,
                        {
                            "irk": record["irk"],
                            "matched_address": normalize_address(
                                record.get("registry_leaf")
                            ),
                            "captured_at": datetime.now(UTC).isoformat(),
                        },
                    )
                    self.mqtt.publish_json(
                        f"{self.mqtt.base}/pairing/result",
                        {
                            "schema": 1,
                            "observer_id": self.config.observer_id,
                            "session_id": session_id,
                            "ciphertext": encrypted,
                        },
                        retain=False,
                    )
                    self._publish_pairing_status(
                        session_id,
                        "identity_captured",
                        "Identita ricevuta; Home Assistant la sta verificando",
                    )
                    return
                if len(new_records) > 1:
                    raise RuntimeError(
                        "Sono comparsi piu dispositivi: ripeti abbinandone uno solo"
                    )
                await asyncio.sleep(2)
            self._publish_pairing_status(
                session_id,
                "timeout",
                "Tempo scaduto senza un nuovo telefono abbinato",
            )
        except asyncio.CancelledError:
            self._publish_pairing_status(
                session_id,
                "cancelled",
                "Abbinamento annullato",
            )
            raise
        except Exception as exc:
            LOGGER.exception("Bluetooth pairing session failed")
            self._publish_pairing_status(
                session_id,
                "error",
                str(exc) or type(exc).__name__,
            )
        finally:
            try:
                await asyncio.to_thread(set_bluetooth_discoverable, False)
            except Exception:
                LOGGER.exception("Unable to disable Bluetooth discoverability")
            if scanner_paused:
                self._resume_scanner_after_pairing()

    def detection_callback(self, device: Any, advertisement: Any) -> None:
        address = normalize_address(getattr(device, "address", None))
        if not address:
            return
        try:
            rssi = int(float(getattr(advertisement, "rssi", -127)))
        except (TypeError, ValueError):
            return
        if rssi < -127 or rssi > 20:
            return
        self._last_detection_monotonic = time.monotonic()
        name = str(
            getattr(advertisement, "local_name", None)
            or getattr(device, "name", None)
            or ""
        ).strip()[:100]
        manufacturer_data = getattr(advertisement, "manufacturer_data", None) or {}
        self.observations[address] = {
            "address": address,
            "name": name or None,
            "rssi": rssi,
            "tx_power": getattr(advertisement, "tx_power", None),
            "service_uuids": sorted(
                str(value).lower()
                for value in (getattr(advertisement, "service_uuids", None) or [])
            )[:MAX_SERVICE_UUIDS],
            "manufacturer_ids": sorted(str(value) for value in manufacturer_data)[
                :MAX_MANUFACTURER_IDS
            ],
            "manufacturer_data": {
                str(company_id): bytes(payload).hex().upper()
                for company_id, payload in list(manufacturer_data.items())[
                    :MAX_MANUFACTURER_IDS
                ]
            },
            "seen_monotonic": time.monotonic(),
        }

    def publish_snapshot(self) -> None:
        now_monotonic = time.monotonic()
        if (
            now_monotonic - self._last_discovery_refresh_monotonic
            >= DISCOVERY_REFRESH_SECONDS
        ):
            self.mqtt.refresh_discovery()
            self._last_discovery_refresh_monotonic = now_monotonic
        cutoff = now_monotonic - self.config.observation_ttl
        self.observations = {
            address: row
            for address, row in self.observations.items()
            if float(row.get("seen_monotonic") or 0.0) >= cutoff
        }
        rows = sorted(
            self.observations.values(),
            key=lambda row: int(row["rssi"]),
            reverse=True,
        )[: self.config.max_observations]
        captured_at = datetime.now(UTC).isoformat()
        payload = {
            "schema": 1,
            "observer_id": self.config.observer_id,
            "name": self.config.name,
            "captured_at": captured_at,
            "timestamp": captured_at,
            "connectable": True,
            "anchor_aliases": [
                f"ble_presence_observer_{self.config.observer_id}",
            ],
            "observations": [
                {key: value for key, value in row.items() if key != "seen_monotonic"}
                for row in rows
            ],
        }
        self.mqtt.publish_json(f"{self.mqtt.base}/observations", payload)
        self.mqtt.publish_bridge_json("observations", payload)
        self.mqtt.publish_bridge_json(
            "status",
            self.mqtt.status_payload(online=True),
        )
        self.mqtt.publish("availability", "online")
        self.mqtt.publish("device_count", str(len(rows)))
        self.mqtt.publish("last_scan", captured_at)
        self.mqtt.publish("error", "none")

    async def run(self) -> None:
        self._loop = asyncio.get_running_loop()
        pairing_loop = asyncio.create_task(self._pairing_command_loop())
        self.mqtt.start()
        try:
            while not self.stop_event.is_set():
                while (
                    self._scanner_pause_requested.is_set()
                    and not self.stop_event.is_set()
                ):
                    self._scanner_stopped.set()
                    await self._sleep_or_stop(0.1)
                if self.stop_event.is_set():
                    break
                self._scanner_stopped.clear()
                try:
                    async with BleakScanner(
                        self.detection_callback,
                        scanning_mode="active",
                    ):
                        LOGGER.info("Bluetooth scan started")
                        await self._scan_loop()
                except asyncio.CancelledError:
                    raise
                except Exception as exc:
                    LOGGER.exception("Bluetooth scan failed")
                    self.mqtt.publish("error", type(exc).__name__)
                    await self._sleep_or_stop(self.config.retry_interval)
                finally:
                    self._scanner_stopped.set()
        finally:
            self._scanner_stopped.set()
            pairing_loop.cancel()
            if self._active_pairing_task is not None:
                self._active_pairing_task.cancel()
                with suppress(asyncio.CancelledError):
                    await self._active_pairing_task
            with suppress(asyncio.CancelledError):
                await pairing_loop
            self.mqtt.stop()

    async def _scan_loop(self) -> None:
        session_started = time.monotonic()
        restart_at = time.monotonic() + self.config.scanner_restart_interval
        first_publish = min(5.0, self.config.publish_interval)
        await self._sleep_or_scan_interrupt(first_publish)
        if self.stop_event.is_set() or self._scanner_pause_requested.is_set():
            return
        self.publish_snapshot()
        while not self.stop_event.is_set():
            remaining = restart_at - time.monotonic()
            if remaining <= 0:
                LOGGER.info("Refreshing the Windows Bluetooth scan session")
                return
            await self._sleep_or_scan_interrupt(
                min(self.config.publish_interval, remaining)
            )
            if self._scanner_pause_requested.is_set():
                return
            if not self.stop_event.is_set():
                now_monotonic = time.monotonic()
                last_activity = max(
                    session_started,
                    self._last_detection_monotonic,
                )
                if scan_session_is_stale(
                    session_started,
                    self._last_detection_monotonic,
                    now_monotonic,
                    self.config.scanner_stale_timeout,
                ):
                    LOGGER.warning(
                        "No BLE advertisements for %.0f seconds; "
                        "refreshing scan session",
                        now_monotonic - last_activity,
                    )
                    self.mqtt.publish("error", "scanner_stale_restart")
                    return
                self.publish_snapshot()

    async def _sleep_or_stop(self, delay: float) -> None:
        with suppress(TimeoutError):
            await asyncio.wait_for(self.stop_event.wait(), timeout=delay)

    async def _sleep_or_scan_interrupt(self, delay: float) -> None:
        """Sleep until shutdown, scanner pause, or the requested delay."""
        stop_task = asyncio.create_task(self.stop_event.wait())
        pause_task = asyncio.create_task(self._scanner_pause_requested.wait())
        try:
            await asyncio.wait(
                {stop_task, pause_task},
                timeout=delay,
                return_when=asyncio.FIRST_COMPLETED,
            )
        finally:
            for task in (stop_task, pause_task):
                if not task.done():
                    task.cancel()
            for task in (stop_task, pause_task):
                with suppress(asyncio.CancelledError):
                    await task


def configure_logging(path: str, verbose: bool) -> None:
    level = logging.DEBUG if verbose else logging.INFO
    formatter = logging.Formatter("%(asctime)s %(levelname)s %(message)s")
    LOGGER.setLevel(level)
    stream = logging.StreamHandler()
    stream.setFormatter(formatter)
    LOGGER.addHandler(stream)
    log_path = Path(path)
    log_path.parent.mkdir(parents=True, exist_ok=True)
    file_handler = RotatingFileHandler(
        log_path,
        maxBytes=2_000_000,
        backupCount=3,
        encoding="utf-8",
    )
    file_handler.setFormatter(formatter)
    LOGGER.addHandler(file_handler)
    gatt_logger = logging.getLogger("presence_bridge.gatt")
    gatt_logger.setLevel(level)
    gatt_logger.handlers.clear()
    gatt_logger.addHandler(stream)
    gatt_logger.addHandler(file_handler)
    gatt_logger.propagate = False


async def async_main(config: ObserverConfig) -> None:
    observer = BlePresenceObserver(config)
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        with suppress(NotImplementedError, RuntimeError):
            loop.add_signal_handler(sig, observer.stop_event.set)
    await observer.run()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", required=True, type=Path)
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args()
    config = ObserverConfig.load(args.config)
    configure_logging(config.log_path, args.verbose)
    try:
        asyncio.run(async_main(config))
    except KeyboardInterrupt:
        return 0
    except Exception:
        LOGGER.exception("Observer stopped unexpectedly")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
