"""Local MQTT coordinator for Presence Bridge."""

from __future__ import annotations

import asyncio
import base64
import hashlib
import io
import json
import re
import secrets
import time
from collections.abc import Callable
from contextlib import suppress
from copy import deepcopy
from datetime import UTC, datetime
from typing import Any

import segno
from bluetooth_data_tools import get_cipher_for_irk, resolve_private_address
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa
from homeassistant.components import mqtt
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import area_registry as ar
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.dispatcher import async_dispatcher_send
from homeassistant.helpers.storage import Store
from homeassistant.util import dt as dt_util

from .const import (
    CONF_AWAY_TIMEOUT,
    CONF_OBSERVER_TIMEOUT,
    DEFAULT_AWAY_TIMEOUT,
    DEFAULT_OBSERVER_TIMEOUT,
    DEFAULT_PAIRING_TIMEOUT,
    DOMAIN,
    GATT_CLAIM_UUID,
    GATT_RESULT_UUID,
    GATT_SERVICE_UUID,
    GATT_SESSION_UUID,
    MAX_PAIRING_TIMEOUT,
    MIN_PAIRING_TIMEOUT,
    MIN_RSSI,
    SIGNAL_IDENTITIES_UPDATED,
    SIGNAL_STATE_UPDATED,
    STORAGE_KEY,
    STORAGE_VERSION,
    TOPIC_OBSERVATIONS,
    TOPIC_PAIRING_RESULT,
    TOPIC_PAIRING_STATUS,
    TOPIC_ROOT,
    TOPIC_STATUS,
)
from .models import IdentityState, ObserverState
from .protocol import PairingLink, b64url_encode

_IRK_RE = re.compile(r"^[0-9A-F]{32}$")
_ACTIVE_PAIRING_STATES = {
    "preparing",
    "advertising",
    "waiting_for_app",
    "bonding",
    "identity_captured",
    "verifying",
}


def _utcnow() -> datetime:
    return datetime.now(UTC)


def _parse_timestamp(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        parsed = dt_util.parse_datetime(str(value))
    except (TypeError, ValueError):
        return None
    return parsed.astimezone(UTC) if parsed else None


def _observer_id_from_topic(topic: str) -> str:
    parts = str(topic).split("/")
    return (
        parts[3]
        if len(parts) >= 5 and parts[:3] == ["presence_bridge", "v1", "observers"]
        else ""
    )


class PresenceBridgeCoordinator:
    """Own observers, private identities and pairing sessions."""

    def __init__(self, hass: HomeAssistant, entry: Any) -> None:
        self.hass = hass
        self.entry = entry
        self.area_registry = ar.async_get(hass)
        self.store: Store[dict[str, Any]] = Store(
            hass,
            STORAGE_VERSION,
            f"{STORAGE_KEY}.{entry.entry_id}",
        )
        self.memory: dict[str, Any] = {"identities": {}, "observer_settings": {}}
        self.observers: dict[str, ObserverState] = {}
        self.identity_states: dict[str, IdentityState] = {}
        self.pairing_public: dict[str, Any] = {
            "state": "idle",
            "active": False,
            "message": "No pairing in progress",
        }
        self._pairing_session: dict[str, Any] | None = None
        self._unsubscribers: list[Callable[[], None]] = []
        self._periodic_task: asyncio.Task[None] | None = None
        self._cipher_cache: dict[str, Any] = {}

    @property
    def away_timeout(self) -> int:
        """Seconds without a resolved advertisement before marking away."""
        return max(
            30,
            int(self.entry.options.get(CONF_AWAY_TIMEOUT, DEFAULT_AWAY_TIMEOUT)),
        )

    @property
    def observer_timeout(self) -> int:
        """Seconds without bridge status before marking it offline."""
        return max(
            30,
            int(
                self.entry.options.get(
                    CONF_OBSERVER_TIMEOUT,
                    DEFAULT_OBSERVER_TIMEOUT,
                )
            ),
        )

    async def async_setup(self) -> None:
        """Load storage and subscribe to the local MQTT protocol."""
        stored = await self.store.async_load()
        if isinstance(stored, dict):
            identities = stored.get("identities")
            settings = stored.get("observer_settings")
            self.memory = {
                "identities": identities if isinstance(identities, dict) else {},
                "observer_settings": settings if isinstance(settings, dict) else {},
            }
        self._rebuild_identity_states()
        subscriptions = (
            (TOPIC_STATUS, self._status_message),
            (TOPIC_OBSERVATIONS, self._observations_message),
            (TOPIC_PAIRING_STATUS, self._pairing_status_message),
            (TOPIC_PAIRING_RESULT, self._pairing_result_message),
        )
        for topic, handler in subscriptions:
            self._unsubscribers.append(
                await mqtt.async_subscribe(self.hass, topic, handler, qos=1)
            )
        self._periodic_task = self.hass.async_create_task(
            self._async_periodic_refresh(),
            f"{DOMAIN}_periodic_refresh",
        )

    async def async_unload(self) -> None:
        """Release subscriptions and stop active pairing."""
        await self.async_cancel_pairing(publish=True)
        for unsubscribe in self._unsubscribers:
            unsubscribe()
        self._unsubscribers.clear()
        if self._periodic_task:
            self._periodic_task.cancel()
            with suppress(asyncio.CancelledError):
                await self._periodic_task
            self._periodic_task = None

    async def _async_periodic_refresh(self) -> None:
        while True:
            await asyncio.sleep(15)
            session = self._pairing_session
            if session and int(session["expires_at"]) <= int(time.time()):
                await self.async_cancel_pairing(publish=True)
                self._set_pairing_state("timeout", "Pairing invitation expired")
            changed = self._expire_runtime_state()
            if changed:
                async_dispatcher_send(self.hass, SIGNAL_STATE_UPDATED)

    def _expire_runtime_state(self) -> bool:
        now = _utcnow()
        changed = False
        for observer in self.observers.values():
            online = bool(
                observer.last_seen
                and (now - observer.last_seen).total_seconds() <= self.observer_timeout
            )
            if observer.online != online:
                observer.online = online
                changed = True
        for state in self.identity_states.values():
            is_home = bool(
                state.last_seen
                and (now - state.last_seen).total_seconds() <= self.away_timeout
            )
            if state.is_home != is_home:
                state.is_home = is_home
                changed = True
        return changed

    @callback
    def _status_message(self, message: Any) -> None:
        payload = self._decode_payload(message)
        observer_id = _observer_id_from_topic(message.topic)
        if not observer_id or not isinstance(payload, dict):
            return
        observer = self._ensure_observer(observer_id, payload)
        observer.name = str(payload.get("name") or observer.name or observer_id)[:100]
        observer.online = bool(payload.get("online", True))
        observer.last_seen = _parse_timestamp(payload.get("timestamp")) or _utcnow()
        observer.version = str(payload.get("version") or observer.version)[:40]
        capabilities = payload.get("capabilities")
        if isinstance(capabilities, list):
            observer.capabilities = [str(value)[:40] for value in capabilities[:20]]
        async_dispatcher_send(self.hass, SIGNAL_STATE_UPDATED)

    @callback
    def _observations_message(self, message: Any) -> None:
        payload = self._decode_payload(message)
        observer_id = _observer_id_from_topic(message.topic)
        if not observer_id or not isinstance(payload, dict):
            return
        observer = self._ensure_observer(observer_id, payload)
        rows = payload.get("observations")
        observer.observations = self._normalize_observations(rows)
        observer.name = str(payload.get("name") or observer.name or observer_id)[:100]
        observer.online = True
        observer.last_seen = _parse_timestamp(payload.get("timestamp")) or _utcnow()
        self._resolve_identities()
        async_dispatcher_send(self.hass, SIGNAL_STATE_UPDATED)

    @staticmethod
    def _decode_payload(message: Any) -> Any:
        try:
            return json.loads(str(message.payload))
        except (TypeError, ValueError, json.JSONDecodeError):
            return None

    def _ensure_observer(
        self,
        observer_id: str,
        payload: dict[str, Any],
    ) -> ObserverState:
        observer = self.observers.get(observer_id)
        settings = self.memory.get("observer_settings", {}).get(observer_id, {})
        if observer is None:
            observer = ObserverState(
                observer_id=observer_id,
                name=str(payload.get("name") or observer_id)[:100],
                area_id=(
                    settings.get("area_id") if isinstance(settings, dict) else None
                ),
            )
            self.observers[observer_id] = observer
        return observer

    @staticmethod
    def _normalize_observations(value: Any) -> list[dict[str, Any]]:
        if not isinstance(value, list):
            return []
        normalized: list[dict[str, Any]] = []
        for row in value[:200]:
            if not isinstance(row, dict):
                continue
            address = "".join(
                character
                for character in str(row.get("address") or "").upper()
                if character in "0123456789ABCDEF"
            )
            if len(address) != 12:
                continue
            try:
                rssi = int(row.get("rssi"))
            except (TypeError, ValueError):
                continue
            if rssi < MIN_RSSI or rssi > 20:
                continue
            normalized.append(
                {
                    "address": ":".join(
                        address[index : index + 2] for index in range(0, 12, 2)
                    ),
                    "rssi": rssi,
                    "name": str(row.get("name") or "")[:100],
                    "seen_at": str(row.get("seen_at") or "")[:40],
                }
            )
        return normalized

    def _rebuild_identity_states(self) -> None:
        previous = self.identity_states
        previous_ids = set(previous)
        self.identity_states = {}
        for identity_id, row in self.memory.get("identities", {}).items():
            if not isinstance(row, dict):
                continue
            person_entity_id = str(row.get("person_entity_id") or "")
            if not person_entity_id.startswith("person."):
                continue
            label = str(row.get("label") or person_entity_id)[:100]
            existing = previous.get(identity_id)
            if existing is not None:
                existing.person_entity_id = person_entity_id
                existing.label = label
                self.identity_states[identity_id] = existing
            else:
                self.identity_states[identity_id] = IdentityState(
                    identity_id=identity_id,
                    person_entity_id=person_entity_id,
                    label=label,
                )
        if set(self.identity_states) != previous_ids:
            async_dispatcher_send(self.hass, SIGNAL_IDENTITIES_UPDATED)

    def _resolve_identities(self) -> None:
        now = _utcnow()
        for identity_id, row in self.memory.get("identities", {}).items():
            if not isinstance(row, dict):
                continue
            irk = str(row.get("irk") or "").upper()
            if not _IRK_RE.fullmatch(irk):
                continue
            matches = self._matches_for_irk(irk)
            if not matches:
                continue
            strongest = max(matches, key=lambda match: match[1])
            observer, rssi = strongest
            state = self.identity_states.get(identity_id)
            if state is None:
                continue
            state.is_home = True
            state.observer_id = observer.observer_id
            state.observer_name = observer.name
            state.area_id = observer.area_id
            state.rssi = rssi
            state.last_seen = now

    def _matches_for_irk(self, irk: str) -> list[tuple[ObserverState, int]]:
        try:
            cipher = self._cipher_cache.setdefault(
                irk,
                get_cipher_for_irk(bytes.fromhex(irk)),
            )
        except (TypeError, ValueError):
            return []
        matches: list[tuple[ObserverState, int]] = []
        for observer in self.observers.values():
            if not observer.online:
                continue
            strongest: int | None = None
            for observation in observer.observations:
                try:
                    matched = resolve_private_address(cipher, observation["address"])
                except (TypeError, ValueError):
                    continue
                if matched:
                    rssi = int(observation["rssi"])
                    strongest = rssi if strongest is None else max(strongest, rssi)
            if strongest is not None:
                matches.append((observer, strongest))
        return matches

    async def async_start_pairing(
        self,
        person_entity_id: str,
        observer_id: str | None = None,
        timeout_seconds: int = DEFAULT_PAIRING_TIMEOUT,
    ) -> dict[str, Any]:
        """Start an app-assisted, one-shot BLE bond session."""
        person_entity_id = str(person_entity_id or "").strip()
        person_state = self.hass.states.get(person_entity_id)
        if not person_entity_id.startswith("person.") or person_state is None:
            raise HomeAssistantError("Select an existing Home Assistant person")
        selected = self._select_observer(observer_id)
        if selected is None:
            raise HomeAssistantError("No online observer supports app-assisted pairing")
        timeout_seconds = max(
            MIN_PAIRING_TIMEOUT,
            min(MAX_PAIRING_TIMEOUT, int(timeout_seconds)),
        )
        await self.async_cancel_pairing(publish=True)

        session_id = secrets.token_urlsafe(24)
        expires_at = int(time.time()) + timeout_seconds
        app_secret = secrets.token_bytes(32)
        private_key = await self.hass.async_add_executor_job(
            lambda: rsa.generate_private_key(public_exponent=65537, key_size=2048)
        )
        public_der = private_key.public_key().public_bytes(
            serialization.Encoding.DER,
            serialization.PublicFormat.SubjectPublicKeyInfo,
        )
        link = PairingLink(
            session_id=session_id,
            observer_id=selected.observer_id,
            expires_at=expires_at,
            secret=app_secret,
        )
        self._pairing_session = {
            "session_id": session_id,
            "observer_id": selected.observer_id,
            "person_entity_id": person_entity_id,
            "person_name": person_state.name,
            "expires_at": expires_at,
            "private_key": private_key,
            "link": link,
        }
        pairing_uri = link.to_uri()
        qr_data_uri = await self.hass.async_add_executor_job(
            self._qr_data_uri,
            pairing_uri,
        )
        self._set_pairing_state(
            "preparing",
            "Starting the secure Bluetooth service",
            person_entity_id=person_entity_id,
            person_name=person_state.name,
            observer_id=selected.observer_id,
            observer_name=selected.name,
            expires_at=expires_at,
            pairing_uri=pairing_uri,
            qr_data_uri=qr_data_uri,
        )
        await mqtt.async_publish(
            self.hass,
            f"{TOPIC_ROOT}/{selected.observer_id}/pairing/command",
            json.dumps(
                {
                    "schema": 2,
                    "action": "start_app_pairing",
                    "session_id": session_id,
                    "observer_id": selected.observer_id,
                    "expires_at": expires_at,
                    "timeout_seconds": timeout_seconds,
                    "app_secret": b64url_encode(app_secret),
                    "public_key": base64.b64encode(public_der).decode("ascii"),
                    "gatt": {
                        "service_uuid": GATT_SERVICE_UUID,
                        "session_uuid": GATT_SESSION_UUID,
                        "claim_uuid": GATT_CLAIM_UUID,
                        "result_uuid": GATT_RESULT_UUID,
                    },
                },
                separators=(",", ":"),
            ),
            qos=1,
            retain=False,
        )
        return self.pairing_payload()

    def _select_observer(self, observer_id: str | None) -> ObserverState | None:
        requested = str(observer_id or "").strip().lower()
        candidates = [
            observer
            for observer in self.observers.values()
            if observer.online and "app_pairing" in observer.capabilities
        ]
        if requested:
            return next(
                (
                    observer
                    for observer in candidates
                    if observer.observer_id == requested
                ),
                None,
            )
        return (
            sorted(candidates, key=lambda observer: observer.name.casefold())[0]
            if candidates
            else None
        )

    @staticmethod
    def _qr_data_uri(value: str) -> str:
        stream = io.BytesIO()
        segno.make(value, error="m").save(
            stream,
            kind="svg",
            scale=6,
            border=2,
            xmldecl=False,
            svgns=True,
        )
        return "data:image/svg+xml;base64," + base64.b64encode(
            stream.getvalue()
        ).decode("ascii")

    async def async_cancel_pairing(self, *, publish: bool) -> None:
        """Cancel the current pairing session and stop GATT advertising."""
        session = self._pairing_session
        if session and publish:
            await mqtt.async_publish(
                self.hass,
                f"{TOPIC_ROOT}/{session['observer_id']}/pairing/command",
                json.dumps(
                    {
                        "schema": 2,
                        "action": "cancel",
                        "session_id": session["session_id"],
                    },
                    separators=(",", ":"),
                ),
                qos=1,
                retain=False,
            )
        self._pairing_session = None
        if self.pairing_public.get("state") != "idle":
            self._set_pairing_state("cancelled", "Pairing cancelled")

    @callback
    def _pairing_status_message(self, message: Any) -> None:
        payload = self._decode_payload(message)
        session = self._pairing_session
        if not isinstance(payload, dict) or not session:
            return
        if (
            payload.get("session_id") != session["session_id"]
            or payload.get("observer_id") != session["observer_id"]
        ):
            return
        state = str(payload.get("state") or "").lower()
        allowed = _ACTIVE_PAIRING_STATES | {"cancelled", "timeout", "error"}
        if state not in allowed:
            return
        self._set_pairing_state(
            state,
            str(payload.get("message") or "Pairing update")[:240],
        )
        if state in {"cancelled", "timeout", "error"}:
            self._pairing_session = None

    @callback
    def _pairing_result_message(self, message: Any) -> None:
        payload = self._decode_payload(message)
        session = self._pairing_session
        if not isinstance(payload, dict) or not session:
            return
        if (
            payload.get("session_id") != session["session_id"]
            or payload.get("observer_id") != session["observer_id"]
        ):
            return
        self.hass.async_create_task(
            self._async_process_pairing_result(payload),
            f"{DOMAIN}_pairing_result",
        )

    async def _async_process_pairing_result(self, payload: dict[str, Any]) -> None:
        session = self._pairing_session
        if not session:
            return
        try:
            ciphertext = base64.b64decode(
                str(payload.get("ciphertext") or ""), validate=True
            )
            plaintext = session["private_key"].decrypt(
                ciphertext,
                padding.OAEP(
                    mgf=padding.MGF1(algorithm=hashes.SHA256()),
                    algorithm=hashes.SHA256(),
                    label=None,
                ),
            )
            result = json.loads(plaintext.decode("utf-8"))
            irk = str(result.get("irk") or "").upper()
            if not _IRK_RE.fullmatch(irk) or not result.get("claim_verified"):
                raise ValueError("Invalid or unverified identity")
        except Exception as err:
            self._set_pairing_state(
                "error",
                f"The bridge returned an invalid identity ({type(err).__name__})",
            )
            self._pairing_session = None
            return

        self._set_pairing_state(
            "verifying", "Verifying the phone's live private address"
        )
        matches: list[tuple[ObserverState, int]] = []
        for _attempt in range(20):
            matches = self._matches_for_irk(irk)
            if matches:
                break
            await asyncio.sleep(3)
        if not matches:
            self._set_pairing_state(
                "error",
                "The bond succeeded, but no matching live phone advertisement was found",
            )
            self._pairing_session = None
            return

        identities = self.memory.setdefault("identities", {})
        for identity_id, row in identities.items():
            if isinstance(row, dict) and row.get("irk") == irk:
                if row.get("person_entity_id") != session["person_entity_id"]:
                    self._set_pairing_state(
                        "error",
                        "This phone is already linked to another person",
                    )
                    self._pairing_session = None
                    return
                selected_id = identity_id
                break
        else:
            selected_id = hashlib.sha256(bytes.fromhex(irk)).hexdigest()[:16]

        identities[selected_id] = {
            "irk": irk,
            "person_entity_id": session["person_entity_id"],
            "label": f"iPhone - {session['person_name']}",
            "created_at": dt_util.now().isoformat(),
            "paired_by": session["observer_id"],
            "protocol": 1,
        }
        await self.store.async_save(self.memory)
        self._cipher_cache.pop(irk, None)
        self._rebuild_identity_states()
        self._resolve_identities()
        self._set_pairing_state(
            "complete",
            f"{session['person_name']}'s iPhone is paired and verified",
            identity_id=selected_id,
        )
        self._pairing_session = None
        async_dispatcher_send(self.hass, SIGNAL_STATE_UPDATED)

    def _set_pairing_state(self, state: str, message: str, **extra: Any) -> None:
        preserved = {
            key: value
            for key, value in self.pairing_public.items()
            if key
            in {
                "person_entity_id",
                "person_name",
                "observer_id",
                "observer_name",
            }
        }
        if state in _ACTIVE_PAIRING_STATES:
            preserved.update(
                {
                    key: value
                    for key, value in self.pairing_public.items()
                    if key in {"expires_at", "pairing_uri", "qr_data_uri"}
                }
            )
        self.pairing_public = {
            **preserved,
            **extra,
            "state": state,
            "active": state in _ACTIVE_PAIRING_STATES,
            "message": message,
            "updated_at": dt_util.now().isoformat(),
        }
        async_dispatcher_send(self.hass, SIGNAL_STATE_UPDATED)

    async def async_set_observer_area(
        self, observer_id: str, area_id: str | None
    ) -> None:
        """Assign a fixed bridge to an HA area."""
        observer_id = str(observer_id or "").strip().lower()
        observer = self.observers.get(observer_id)
        if observer is None:
            raise HomeAssistantError("Unknown Presence Bridge observer")
        area_id = str(area_id or "").strip() or None
        if area_id and self.area_registry.async_get_area(area_id) is None:
            raise HomeAssistantError("Unknown Home Assistant area")
        settings = self.memory.setdefault("observer_settings", {}).setdefault(
            observer_id, {}
        )
        settings["area_id"] = area_id
        observer.area_id = area_id
        await self.store.async_save(self.memory)
        self._resolve_identities()
        async_dispatcher_send(self.hass, SIGNAL_STATE_UPDATED)

    async def async_remove_identity(self, identity_id: str) -> None:
        """Forget one private identity without touching the Windows bond."""
        identities = self.memory.setdefault("identities", {})
        identity_id = str(identity_id)
        if identity_id not in identities:
            raise HomeAssistantError("Unknown Presence Bridge identity")
        identities.pop(identity_id)
        await self.store.async_save(self.memory)
        self._rebuild_identity_states()
        entity_registry = er.async_get(self.hass)
        for platform, unique_id in (
            ("binary_sensor", f"{identity_id}_presence"),
            ("device_tracker", f"{identity_id}_tracker"),
            ("sensor", f"{identity_id}_room"),
        ):
            entity_id = entity_registry.async_get_entity_id(
                platform,
                DOMAIN,
                unique_id,
            )
            if entity_id:
                entity_registry.async_remove(entity_id)
        device_registry = dr.async_get(self.hass)
        device = device_registry.async_get_device(identifiers={(DOMAIN, identity_id)})
        if device:
            device_registry.async_remove_device(device.id)
        async_dispatcher_send(self.hass, SIGNAL_STATE_UPDATED)

    def pairing_payload(self) -> dict[str, Any]:
        """Return the active pairing state for an authenticated HA client."""
        return deepcopy(self.pairing_public)

    def public_payload(self, *, include_invitation: bool = False) -> dict[str, Any]:
        """Return a redacted UI payload; IRKs are never included."""
        areas = {area.id: area.name for area in self.area_registry.async_list_areas()}
        observers = [
            {
                "observer_id": observer.observer_id,
                "name": observer.name,
                "online": observer.online,
                "area_id": observer.area_id,
                "area_name": areas.get(observer.area_id or ""),
                "last_seen": observer.last_seen.isoformat()
                if observer.last_seen
                else None,
                "capabilities": list(observer.capabilities),
                "version": observer.version,
                "observation_count": len(observer.observations),
            }
            for observer in sorted(
                self.observers.values(), key=lambda item: item.name.casefold()
            )
        ]
        identities = [
            self.identity_payload(identity_id)
            for identity_id in sorted(self.identity_states)
        ]
        pairing = self.pairing_payload()
        if not include_invitation:
            pairing.pop("pairing_uri", None)
            pairing.pop("qr_data_uri", None)
        people = [
            {"entity_id": state.entity_id, "name": state.name}
            for state in sorted(
                self.hass.states.async_all("person"),
                key=lambda item: item.name.casefold(),
            )
        ]
        return {
            "version": 1,
            "observers": observers,
            "identities": identities,
            "people": people,
            "areas": [
                {"area_id": area_id, "name": name}
                for area_id, name in sorted(
                    areas.items(), key=lambda item: item[1].casefold()
                )
            ],
            "pairing": pairing,
        }

    def identity_payload(self, identity_id: str) -> dict[str, Any]:
        """Return one redacted entity snapshot."""
        state = self.identity_states[identity_id]
        area = (
            self.area_registry.async_get_area(state.area_id) if state.area_id else None
        )
        return {
            "identity_id": state.identity_id,
            "person_entity_id": state.person_entity_id,
            "label": state.label,
            "is_home": state.is_home,
            "observer_id": state.observer_id,
            "observer_name": state.observer_name,
            "area_id": state.area_id,
            "area_name": area.name if area else None,
            "rssi": state.rssi,
            "last_seen": state.last_seen.isoformat() if state.last_seen else None,
        }

    def diagnostics_payload(self) -> dict[str, Any]:
        """Return privacy-safe integration diagnostics."""
        payload = self.public_payload(include_invitation=False)
        observer_ids = {
            observer["observer_id"]: f"OBSERVER_{index}"
            for index, observer in enumerate(payload["observers"], start=1)
        }
        for observer in payload["observers"]:
            observer["observer_id"] = observer_ids[observer["observer_id"]]
            observer["name"] = "REDACTED"
            observer["area_id"] = "REDACTED" if observer["area_id"] else None
            observer["area_name"] = "REDACTED" if observer["area_name"] else None
        for index, identity in enumerate(payload["identities"], start=1):
            identity["identity_id"] = f"IDENTITY_{index}"
            identity["person_entity_id"] = "REDACTED"
            identity["label"] = "REDACTED"
            identity["observer_id"] = observer_ids.get(
                identity.get("observer_id"), identity.get("observer_id")
            )
            identity["observer_name"] = (
                "REDACTED" if identity.get("observer_name") else None
            )
            identity["area_id"] = "REDACTED" if identity.get("area_id") else None
            identity["area_name"] = "REDACTED" if identity.get("area_name") else None
        payload["people"] = [
            {"entity_id": "REDACTED", "name": "REDACTED"}
            for _person in payload["people"]
        ]
        payload["areas"] = [
            {"area_id": "REDACTED", "name": "REDACTED"} for _area in payload["areas"]
        ]
        pairing = payload["pairing"]
        for key in (
            "person_entity_id",
            "person_name",
            "observer_name",
            "identity_id",
            "message",
        ):
            if pairing.get(key):
                pairing[key] = "REDACTED"
        if pairing.get("observer_id"):
            pairing["observer_id"] = observer_ids.get(
                pairing["observer_id"], "REDACTED"
            )
        return payload
