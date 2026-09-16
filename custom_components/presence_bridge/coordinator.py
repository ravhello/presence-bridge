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
from urllib.parse import urlencode

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
    PAIRING_COMPLETION_TIMEOUT,
    PAIRING_HANDOFF_TIMEOUT,
    SIGNAL_IDENTITIES_UPDATED,
    SIGNAL_STATE_UPDATED,
    STORAGE_KEY,
    STORAGE_VERSION,
    TOPIC_IDENTITY_REMOVAL_RESULT,
    TOPIC_OBSERVATIONS,
    TOPIC_PAIRING_RESULT,
    TOPIC_PAIRING_STATUS,
    TOPIC_ROOT,
    TOPIC_STATUS,
)
from .local_receiver import LocalReceiver
from .models import IdentityState, ObserverState
from .native_bluetooth import refresh_native_observations
from .protocol import PairingLink, b64url_encode

_IRK_RE = re.compile(r"^[0-9A-F]{32}$")
_PAIRING_SESSION_ID_RE = re.compile(r"^[A-Za-z0-9_-]{16,96}$")
_ACTIVE_PAIRING_STATES = {
    "preparing",
    "advertising",
    "waiting_for_app",
    "connecting",
    "bonding",
    "identity_captured",
    "verifying",
}
_PAIRING_COMPLETION_CODES = {
    "iphone_session_verified",
    "iphone_bond_ready",
    "iphone_bond_settling",
    "iphone_bond_reconnecting",
    "iphone_claim_received",
    "iphone_claim_accepted",
    "iphone_ack_deferred",
    "identity_captured",
}
_PAIRING_HANDOFF_CODES = {
    "iphone_advertisement_seen",
    "iphone_candidate_unverified",
    "receiver_proximity_confirmed",
}
_FORCED_RENEWAL_COALESCE_SECONDS = 30.0


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


def _pairing_deadline(session: dict[str, Any]) -> int:
    """Return the active deadline for an invitation or claimed attempt."""
    for key in ("completion_expires_at", "attempt_expires_at", "expires_at"):
        try:
            value = int(session.get(key) or 0)
        except (TypeError, ValueError):
            continue
        if value > 0:
            return value
    return 0


def _bounded_lease_deadline(value: Any, now: int, maximum: int) -> int | None:
    """Accept only a short receiver-issued lease near the current time."""
    try:
        deadline = int(value)
    except (TypeError, ValueError):
        return None
    if now - 5 < deadline <= now + maximum + 30:
        return min(deadline, now + maximum)
    return None


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
        self._pairing_lock = asyncio.Lock()
        self._unsubscribers: list[Callable[[], None]] = []
        self._periodic_task: asyncio.Task[None] | None = None
        self._cipher_cache: dict[str, Any] = {}
        self._orphan_pairing_cancels: set[tuple[str, str]] = set()
        self._removal_requests: dict[str, dict[str, Any]] = {}
        self.local_receiver = LocalReceiver(self)

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
        refresh_native_observations(self)
        subscriptions = (
            (TOPIC_STATUS, self._status_message),
            (TOPIC_OBSERVATIONS, self._observations_message),
            (TOPIC_PAIRING_STATUS, self._pairing_status_message),
            (TOPIC_PAIRING_RESULT, self._pairing_result_message),
            (TOPIC_IDENTITY_REMOVAL_RESULT, self._identity_removal_result_message),
        )
        if self.hass.config_entries.async_entries("mqtt"):
            for topic, handler in subscriptions:
                self._unsubscribers.append(
                    await mqtt.async_subscribe(self.hass, topic, handler, qos=1)
                )
        if self.entry.options.get("local_receiver", False):
            await self.local_receiver.start()
        self._periodic_task = self.hass.async_create_background_task(
            self._async_periodic_refresh(),
            f"{DOMAIN}_periodic_refresh",
        )

    async def async_unload(self) -> None:
        """Release subscriptions and stop active pairing."""
        for request in self._removal_requests.values():
            request["future"].cancel()
        await self.async_cancel_pairing(publish=True)
        await self.local_receiver.close()
        for unsubscribe in self._unsubscribers:
            unsubscribe()
        self._unsubscribers.clear()
        if self._periodic_task:
            self._periodic_task.cancel()
            with suppress(asyncio.CancelledError):
                await self._periodic_task
            self._periodic_task = None

    async def _async_publish(self, hass, topic, payload, *, qos=1, retain=False):
        """Use the same protocol in-process, or deliver to a remote receiver."""
        receiver = self.local_receiver.receiver
        if receiver and topic == f"{TOPIC_ROOT}/{receiver.observer_id}/pairing/command":
            await receiver.command(json.loads(payload), retained=retain)
            return
        if not self.hass.config_entries.async_entries("mqtt"):
            raise HomeAssistantError(
                "Configure MQTT for a remote receiver, or enable local Linux enrollment"
            )
        await mqtt.async_publish(hass, topic, payload, qos=qos, retain=retain)

    async def _async_periodic_refresh(self) -> None:
        while True:
            await asyncio.sleep(5)
            session = self._pairing_session
            if session and _pairing_deadline(session) <= int(time.time()):
                if session.get("completion_expires_at"):
                    message = "Pairing stopped after five minutes without completion"
                elif session.get("attempt_expires_at"):
                    message = "The iPhone was found, but its QR session was not verified in time"
                else:
                    message = "Pairing code expired before an iPhone started"
                await self.async_cancel_pairing(publish=True)
                self._set_pairing_state("timeout", message)
            refresh_native_observations(self)
            self._expire_runtime_state()
            self._resolve_identities()
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
            if not online:
                observer.observations.clear()
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
        observer.last_seen = (
            (_parse_timestamp(payload.get("timestamp")) or _utcnow())
            if observer.online
            else None
        )
        if not observer.online:
            observer.observations.clear()
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
        observer.observations = self._normalize_observations(
            rows, payload.get("timestamp") or payload.get("captured_at")
        )
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
    def _normalize_observations(
        value: Any, captured_at: Any = None
    ) -> list[dict[str, Any]]:
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
                    "seen_at": str(row.get("seen_at") or captured_at or "")[:40],
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
        for identity_id, row in self.memory.get("identities", {}).items():
            if not isinstance(row, dict):
                continue
            irk = str(row.get("irk") or "").upper()
            if not _IRK_RE.fullmatch(irk):
                continue
            matches = self._matches_for_irk(irk)
            if not matches:
                continue
            newest = max(match[2] for match in matches)
            matches = [m for m in matches if (newest - m[2]).total_seconds() <= 30]
            strongest = max(matches, key=lambda match: match[1])
            observer, rssi, _seen = strongest
            state = self.identity_states.get(identity_id)
            if state is None:
                continue
            current = next(
                (m for m in matches if m[0].observer_id == state.observer_id), None
            )
            if current is not None and current[1] + 6 >= rssi:
                observer, rssi, _seen = current
            state.is_home = True
            state.observer_id = observer.observer_id
            state.observer_name = observer.name
            state.area_id = observer.area_id
            state.rssi = rssi
            state.signal_seen_at = _seen
            state.last_seen = max(match[2] for match in matches)

    def _matches_for_irk(
        self, irk: str, *, identity_address=None, paired_by=None
    ) -> list[tuple[ObserverState, int, datetime]]:
        if not identity_address:
            for row in self.memory.get("identities", {}).values():
                if isinstance(row, dict) and row.get("irk") == irk:
                    identity_address, paired_by = (
                        row.get("identity_address"),
                        row.get("paired_by"),
                    )
                    break
        try:
            cipher = self._cipher_cache.get(irk)
            if cipher is None:
                cipher = self._cipher_cache[irk] = get_cipher_for_irk(
                    bytes.fromhex(irk)
                )
        except (TypeError, ValueError):
            return []
        matches: list[tuple[ObserverState, int, datetime]] = []
        now = _utcnow()
        for observer in self.observers.values():
            if not observer.online:
                continue
            strongest: int | None = None
            newest: datetime | None = None
            for observation in observer.observations:
                seen = _parse_timestamp(observation.get("seen_at"))
                if (
                    seen is None
                    or not 0 <= (now - seen).total_seconds() <= self.away_timeout
                ):
                    continue
                try:
                    # BlueZ may resolve RPAs in the kernel. Accept its identity
                    # address only on the receiver that verified this bond.
                    matched = bool(
                        identity_address
                        and observer.observer_id == paired_by
                        and observation["address"] == identity_address
                    )
                    if not matched:
                        matched = resolve_private_address(
                            cipher, observation["address"]
                        )
                except (TypeError, ValueError):
                    continue
                if matched:
                    rssi = int(observation["rssi"])
                    # An older RPA must not hold the signal at its old peak.
                    if newest is None or seen > newest:
                        strongest, newest = rssi, seen
                    elif seen == newest:
                        strongest = max(strongest, rssi)
            if strongest is not None and newest is not None:
                matches.append((observer, strongest, newest))
        return matches

    async def async_start_pairing(
        self,
        person_entity_id: str,
        observer_id: str | None = None,
        timeout_seconds: int = DEFAULT_PAIRING_TIMEOUT,
        *,
        force_new: bool = False,
    ) -> dict[str, Any]:
        """Start an app-assisted, one-shot BLE bond session."""
        async with self._pairing_lock:
            return await self._async_start_pairing_locked(
                person_entity_id,
                observer_id,
                timeout_seconds,
                force_new=force_new,
            )

    async def _async_start_pairing_locked(
        self,
        person_entity_id: str,
        observer_id: str | None,
        timeout_seconds: int,
        *,
        force_new: bool,
    ) -> dict[str, Any]:
        """Create a pairing session while renewal requests are serialized."""
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
        current = self._pairing_session
        if current is not None:
            same_target = (
                current.get("person_entity_id") == person_entity_id
                and current.get("observer_id") == selected.observer_id
            )
            still_valid = _pairing_deadline(current) > int(time.time()) + 10
            started_monotonic = float(current.get("started_monotonic") or 0.0)
            recent_forced_renewal = bool(
                force_new
                and started_monotonic
                and time.monotonic() - started_monotonic
                < _FORCED_RENEWAL_COALESCE_SECONDS
            )
            if same_target and still_valid and (not force_new or recent_forced_renewal):
                return self.pairing_payload()
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
            "started_monotonic": time.monotonic(),
            "private_key": private_key,
            "link": link,
        }
        pairing_uri = f"{link.to_uri()}&{urlencode({'oname': selected.name[:100]})}"
        qr_data_uri = await self.hass.async_add_executor_job(
            self._qr_data_uri,
            pairing_uri,
        )
        self._set_pairing_state(
            "preparing",
            "Preparing the receiver to find the iPhone",
            person_entity_id=person_entity_id,
            person_name=person_state.name,
            observer_id=selected.observer_id,
            observer_name=selected.name,
            expires_at=expires_at,
            effective_expires_at=expires_at,
            handoff_started=False,
            invitation_consumed=False,
            pairing_uri=pairing_uri,
            qr_data_uri=qr_data_uri,
        )
        await self._async_publish(
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
                    "handoff_timeout_seconds": PAIRING_HANDOFF_TIMEOUT,
                    "completion_timeout_seconds": PAIRING_COMPLETION_TIMEOUT,
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
        """Cancel the current pairing session and stop Bluetooth enrollment."""
        session = self._pairing_session
        if session and publish:
            await self._async_publish(
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
        if not isinstance(payload, dict):
            return
        state = str(payload.get("state") or "").lower()
        session = self._pairing_session
        if not session:
            observer_id = str(payload.get("observer_id") or "").strip().lower()
            session_id = str(payload.get("session_id") or "").strip()
            topic_observer_id = _observer_id_from_topic(message.topic)
            orphan = (observer_id, session_id)
            if (
                state in _ACTIVE_PAIRING_STATES
                and observer_id == topic_observer_id
                and _PAIRING_SESSION_ID_RE.fullmatch(session_id)
                and orphan not in self._orphan_pairing_cancels
            ):
                self._orphan_pairing_cancels.add(orphan)
                self.hass.async_create_task(
                    self._async_cancel_orphaned_pairing(observer_id, session_id),
                    f"{DOMAIN}_cancel_orphaned_pairing",
                )
            return
        if (
            payload.get("session_id") != session["session_id"]
            or payload.get("observer_id") != session["observer_id"]
        ):
            return
        observer = self.observers.get(session["observer_id"])
        if observer is not None:
            # A pairing status packet is also a live heartbeat. The normal BLE
            # observation stream is paused while Windows owns the adapter for
            # GATT, so it must not make the receiver appear offline in the UI.
            observer.online = True
            observer.last_seen = _utcnow()
        allowed = _ACTIVE_PAIRING_STATES | {"cancelled", "timeout", "error"}
        if state not in allowed:
            return
        detail_code = str(payload.get("detail_code") or "")[:120]
        now = int(time.time())
        status_extra: dict[str, Any] = {
            key: str(payload[key])[:120]
            for key in (
                "detail_code",
                "advertisement_status",
                "advertisement_error",
                "gatt_host",
                "transport",
                "rssi",
            )
            if payload.get(key) is not None
        }

        attempt_expires_at = _bounded_lease_deadline(
            payload.get("attempt_expires_at"),
            now,
            PAIRING_HANDOFF_TIMEOUT,
        )
        if (
            attempt_expires_at is None
            and detail_code in _PAIRING_HANDOFF_CODES
            and not session.get("attempt_expires_at")
            and not session.get("completion_expires_at")
        ):
            # Matching either QR-derived radio service proves that pairing
            # started in time. Preserve the attempt even if an intermediate
            # receiver status packet was overwritten before MQTT publication.
            attempt_expires_at = now + PAIRING_HANDOFF_TIMEOUT
        if attempt_expires_at is not None and not session.get("completion_expires_at"):
            attempt_expires_at = min(
                attempt_expires_at,
                session.get("attempt_expires_at") or attempt_expires_at,
            )
            session["attempt_expires_at"] = attempt_expires_at
            status_extra["attempt_expires_at"] = attempt_expires_at
            status_extra["handoff_started"] = True
            status_extra["invitation_consumed"] = True
            status_extra["pairing_uri"] = None
            status_extra["qr_data_uri"] = None

        completion_expires_at = _bounded_lease_deadline(
            payload.get("completion_expires_at"),
            now,
            PAIRING_COMPLETION_TIMEOUT,
        )
        if (
            completion_expires_at is None
            and detail_code in _PAIRING_COMPLETION_CODES
            and not session.get("completion_expires_at")
        ):
            completion_expires_at = (
                session.get("attempt_expires_at") or now + PAIRING_COMPLETION_TIMEOUT
            )
        if completion_expires_at is not None:
            completion_expires_at = min(
                completion_expires_at,
                session.get("attempt_expires_at") or completion_expires_at,
                session.get("completion_expires_at") or completion_expires_at,
            )
            session["completion_expires_at"] = completion_expires_at
            status_extra.update(
                {
                    "completion_expires_at": completion_expires_at,
                    "effective_expires_at": completion_expires_at,
                    "invitation_consumed": True,
                    "pairing_uri": None,
                    "qr_data_uri": None,
                }
            )
        elif session.get("completion_expires_at"):
            status_extra.update(
                {
                    "completion_expires_at": session["completion_expires_at"],
                    "effective_expires_at": session["completion_expires_at"],
                    "invitation_consumed": True,
                    "pairing_uri": None,
                    "qr_data_uri": None,
                }
            )
        elif session.get("attempt_expires_at"):
            status_extra.update(
                {
                    "attempt_expires_at": session["attempt_expires_at"],
                    "effective_expires_at": session["attempt_expires_at"],
                    "handoff_started": True,
                    "invitation_consumed": True,
                    "pairing_uri": None,
                    "qr_data_uri": None,
                }
            )
        self._set_pairing_state(
            state,
            str(payload.get("message") or "Pairing update")[:240],
            **status_extra,
        )
        if state in {"cancelled", "timeout", "error"}:
            self._pairing_session = None

    async def _async_cancel_orphaned_pairing(
        self,
        observer_id: str,
        session_id: str,
    ) -> None:
        """Stop a receiver session whose HA private key was lost on restart."""
        await self._async_publish(
            self.hass,
            f"{TOPIC_ROOT}/{observer_id}/pairing/command",
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
        if (
            not session
            or session.get("result_processing")
            or payload.get("session_id") != session["session_id"]
            or payload.get("observer_id") != session["observer_id"]
        ):
            return
        session["result_processing"] = True
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
            identity_address = result.get("identity_address")
            if identity_address is not None and (
                not isinstance(identity_address, str)
                or not re.fullmatch(r"[0-9A-F]{2}(?::[0-9A-F]{2}){5}", identity_address)
                or result.get("secure_exchange_complete") is not True
            ):
                raise ValueError("Unverified resolved identity")
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
            if self._pairing_session is not session:
                return
            deadline = (
                session.get("completion_expires_at")
                or session.get("attempt_expires_at")
                or session["expires_at"]
            )
            if time.time() >= deadline:
                break
            matches = self._matches_for_irk(
                irk, identity_address=identity_address, paired_by=session["observer_id"]
            )
            if matches:
                break
            await asyncio.sleep(3)
        if self._pairing_session is not session:
            return
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
            "protocol": 2,
            "identity_address": identity_address,
        }
        await self.store.async_save(self.memory)
        if self._pairing_session is not session:
            return
        self._cipher_cache.pop(irk, None)
        self._rebuild_identity_states()
        self._resolve_identities()
        await self._async_publish(
            self.hass,
            f"{TOPIC_ROOT}/{session['observer_id']}/pairing/command",
            json.dumps(
                {"schema": 2, "action": "complete", "session_id": session["session_id"]}
            ),
            qos=1,
            retain=False,
        )
        if self._pairing_session is not session:
            return
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
                    if key
                    in {
                        "expires_at",
                        "attempt_expires_at",
                        "completion_expires_at",
                        "effective_expires_at",
                        "handoff_started",
                        "invitation_consumed",
                        "pairing_uri",
                        "qr_data_uri",
                    }
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
        """Unpair on the owning receiver before removing the HA association."""
        async with self._pairing_lock:
            await self._async_remove_identity_locked(str(identity_id))

    async def _async_remove_identity_locked(self, identity_id: str) -> None:
        identities = self.memory.setdefault("identities", {})
        if identity_id not in identities:
            raise HomeAssistantError("Unknown Presence Bridge identity")
        if self._pairing_session is not None:
            raise HomeAssistantError(
                "Finish or cancel the active pairing before removing a phone"
            )
        row = identities[identity_id]
        observer_id = str(row.get("paired_by") or "")
        observer = self.observers.get(observer_id)
        if observer is None or not observer.online:
            raise HomeAssistantError(
                "The receiver that paired this phone is offline; nothing was removed from HA"
            )
        if "identity_removal" not in observer.capabilities:
            raise HomeAssistantError(
                "Update the Windows receiver before removing this Bluetooth association"
            )
        fingerprint = hashlib.sha256(bytes.fromhex(row["irk"])).hexdigest()
        if fingerprint[:16] != identity_id:
            raise HomeAssistantError("Private identity mismatch; removal stopped")
        request_id = secrets.token_urlsafe(24)
        future = asyncio.get_running_loop().create_future()
        self._removal_requests[request_id] = {
            "observer_id": observer_id,
            "identity_id": identity_id,
            "fingerprint": fingerprint,
            "future": future,
        }
        try:
            await self._async_publish(
                self.hass,
                f"{TOPIC_ROOT}/{observer_id}/pairing/command",
                json.dumps(
                    {
                        "action": "forget_identity",
                        "request_id": request_id,
                        "observer_id": observer_id,
                        "identity_id": identity_id,
                        "fingerprint": fingerprint,
                        "expires_at": time.time() + 75,
                    }
                ),
                qos=1,
                retain=False,
            )
            try:
                result = await asyncio.wait_for(future, timeout=70)
            except TimeoutError as error:
                raise HomeAssistantError(
                    "The receiver did not confirm removal; retry to verify Windows before clearing HA"
                ) from error
            if result.get("success") is not True:
                raise HomeAssistantError(
                    result.get("message")
                    or "Windows Bluetooth removal failed; the HA association was preserved"
                )
        finally:
            self._removal_requests.pop(request_id, None)
        identities.pop(identity_id)
        try:
            await self.store.async_save(self.memory)
        except Exception:
            identities[identity_id] = row
            raise
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
        self._cipher_cache.pop(row["irk"], None)
        if self.pairing_public.get("identity_id") == identity_id:
            self._set_pairing_state(
                "idle",
                "Phone removed from Home Assistant and the receiver; ready for a new pairing",
            )
        async_dispatcher_send(self.hass, SIGNAL_STATE_UPDATED)

    @callback
    def _identity_removal_result_message(self, message: Any) -> None:
        payload = self._decode_payload(message)
        if not isinstance(payload, dict):
            return
        pending = self._removal_requests.get(str(payload.get("request_id") or ""))
        if not pending or any(
            payload.get(key) != pending[key]
            for key in ("observer_id", "identity_id", "fingerprint")
        ):
            return
        if _observer_id_from_topic(message.topic) != pending["observer_id"]:
            return
        if not pending["future"].done():
            pending["future"].set_result(payload)

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
            "local_receiver": self.local_receiver.status,
        }

    def identity_payload(self, identity_id: str) -> dict[str, Any]:
        """Return one redacted entity snapshot."""
        state = self.identity_states[identity_id]
        room_fresh = bool(
            state.is_home
            and state.signal_seen_at
            and 0 <= (_utcnow() - state.signal_seen_at).total_seconds() <= 45
        )
        signal = self.signal_payload(identity_id)
        area = (
            self.area_registry.async_get_area(state.area_id) if state.area_id else None
        )
        return {
            "identity_id": state.identity_id,
            "person_entity_id": state.person_entity_id,
            "label": state.label,
            "is_home": state.is_home,
            "observer_id": state.observer_id,
            "observer_name": state.observer_name if room_fresh else None,
            "area_id": state.area_id if room_fresh else None,
            "area_name": area.name if area and room_fresh else None,
            "rssi": signal["rssi"],
            "signal_seen_at": signal["sampled_at"],
            "signal_fresh": signal["fresh"],
            "signal_receivers": signal["receivers"],
            "presence_evidence": "detected" if state.is_home else "not_detected",
            "source_integration": DOMAIN,
            "person_link_status": self.memory.get("identities", {})
            .get(identity_id, {})
            .get("person_link_status", "pending"),
            "room_fresh": room_fresh,
            "last_known_area": area.name if area else None,
            "receiver_count": len(signal["receivers"]),
            "last_seen": state.last_seen.isoformat() if state.last_seen else None,
        }

    def signal_payload(self, identity_id: str) -> dict[str, Any]:
        """Read-only, per-phone telemetry. No keys, addresses or other people."""
        state = self.identity_states[identity_id]
        row = self.memory.get("identities", {}).get(identity_id, {})
        now = _utcnow()
        receivers = []
        for observer, rssi, seen in self._matches_for_irk(str(row.get("irk", ""))):
            if (now - seen).total_seconds() > 45:
                continue
            area = (
                self.area_registry.async_get_area(observer.area_id)
                if observer.area_id
                else None
            )
            receivers.append(
                {
                    "observer_id": observer.observer_id,
                    "name": observer.name,
                    "area_name": area.name if area else None,
                    "rssi": rssi,
                    "sampled_at": seen.isoformat(),
                }
            )
        receivers.sort(key=lambda item: item["rssi"], reverse=True)
        selected = next(
            (r for r in receivers if r["observer_id"] == state.observer_id), None
        )
        return {
            "version": 1,
            "label": state.label,
            "rssi": selected["rssi"] if selected else None,
            "sampled_at": state.signal_seen_at.isoformat()
            if state.signal_seen_at
            else None,
            "fresh": selected is not None,
            "fresh_for_seconds": 45,
            "poll_after_seconds": 3,
            "server_time": now.isoformat(),
            "receivers": receivers,
            "distance_m": None,
            "distance_status": "not_calibrated",
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
            identity.pop("signal_receivers", None)
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
            identity["last_known_area"] = (
                "REDACTED" if identity.get("last_known_area") else None
            )
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
