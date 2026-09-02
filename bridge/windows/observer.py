#!/usr/bin/env python3
"""Windows Bluetooth observer and secure pairing bridge.

The observer runs on an always-on Windows host, collects nearby BLE
advertisements and publishes a bounded RSSI snapshot to Home Assistant over
MQTT. Normal operation is passive; a temporary GATT service is created only
after an authenticated administrator starts an app-assisted pairing session.
"""

from __future__ import annotations

import argparse
import asyncio
import base64
import ctypes
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
from gatt_server import GattPairingServer
from protocol import PairingLink, ProtocolError, b64url_decode

LOGGER = logging.getLogger("ble_presence_observer")
BRIDGE_VERSION = "0.1.0"
OBSERVER_ID_RE = re.compile(r"^[a-z0-9_]{3,64}$")
MAX_SERVICE_UUIDS = 12
MAX_MANUFACTURER_IDS = 12
DISCOVERY_REFRESH_SECONDS = 60.0
PAIRING_SESSION_ID_RE = re.compile(r"^[A-Za-z0-9_-]{16,96}$")
PRIVATE_BLE_REGISTRY_PATH = r"SYSTEM\CurrentControlSet\Services\BTHPORT\Parameters\Keys"
MAX_PAIRING_TIMEOUT_SECONDS = 600
MIN_PAIRING_TIMEOUT_SECONDS = 60


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
            if "irk" not in value_name.casefold():
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
    ciphertext = public_key.encrypt(
        plaintext,
        padding.OAEP(
            mgf=padding.MGF1(algorithm=hashes.SHA256()),
            algorithm=hashes.SHA256(),
            label=None,
        ),
    )
    return base64.b64encode(ciphertext).decode("ascii")


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
        self.mqtt.set_command_handler(self._receive_pairing_command)

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

    async def _pairing_command_loop(self) -> None:
        while not self.stop_event.is_set():
            payload = await self._pairing_commands.get()
            action = str(payload.get("action") or "").strip().lower()
            session_id = str(payload.get("session_id") or "").strip()
            if action == "cancel":
                if (
                    self._active_pairing_task is not None
                    and not self._active_pairing_task.done()
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
            if self._active_pairing_task is not None:
                self._active_pairing_task.cancel()
                with suppress(asyncio.CancelledError):
                    await self._active_pairing_task
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
        """Pair through an encrypted GATT write initiated by Presence Pair."""
        server = GattPairingServer(
            service_uuid=str(gatt["service_uuid"]),
            session_uuid=str(gatt["session_uuid"]),
            claim_uuid=str(gatt["claim_uuid"]),
            result_uuid=str(gatt["result_uuid"]),
        )
        try:
            baseline = await asyncio.to_thread(read_windows_private_ble_irks)
            await server.async_start(link)
            self._publish_pairing_status(
                link.session_id,
                "waiting_for_app",
                "Scan the code with Presence Pair",
                expires_at=link.expires_at,
            )
            await server.async_wait_for_claim(timeout_seconds)
            self._publish_pairing_status(
                link.session_id,
                "bonding",
                "Encrypted claim accepted; capturing the private identity",
            )
            deadline = time.monotonic() + min(30, timeout_seconds)
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
                            "claim_verified": True,
                        },
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
                    )
                    return
                if len(new_records) > 1:
                    raise RuntimeError("More than one new Bluetooth identity appeared")
                await asyncio.sleep(1)
            raise RuntimeError(
                "The encrypted bond did not create a new Windows identity"
            )
        except TimeoutError:
            self._publish_pairing_status(
                link.session_id,
                "timeout",
                "Pairing invitation expired",
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
            )
        finally:
            await server.async_stop()

    async def _run_pairing_session(
        self,
        session_id: str,
        public_key: str,
        timeout_seconds: int,
    ) -> None:
        try:
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
        await self._sleep_or_stop(first_publish)
        if self.stop_event.is_set():
            return
        self.publish_snapshot()
        while not self.stop_event.is_set():
            remaining = restart_at - time.monotonic()
            if remaining <= 0:
                LOGGER.info("Refreshing the Windows Bluetooth scan session")
                return
            await self._sleep_or_stop(min(self.config.publish_interval, remaining))
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
