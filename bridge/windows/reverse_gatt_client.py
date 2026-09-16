"""Connect to the short-lived GATT service advertised by Presence Pair."""

from __future__ import annotations

import asyncio
import json
import logging
import sys
import time
from collections.abc import Awaitable, Callable
from contextlib import suppress
from dataclasses import dataclass
from typing import Any

from bleak import BleakClient, BleakScanner
from bleak.exc import (
    BleakBluetoothNotAvailableError,
    BleakCharacteristicNotFoundError,
    BleakError,
    BleakGATTProtocolError,
)
from identity_removal import (
    compact_address,
    list_bonded_devices,
    select_bond_devices,
    unpair_device,
)
from protocol import (
    PairingLink,
    acceptance_proof,
    pairing_service_uuid,
    verify_claim,
)

if sys.platform == "win32":
    from winrt.windows.devices.bluetooth.genericattributeprofile import (
        GattProtectionLevel as WinRTGattProtectionLevel,
    )
    from winrt.windows.devices.enumeration import (
        DeviceInformation as WinRTDeviceInformation,
    )
else:
    WinRTDeviceInformation = None
    WinRTGattProtectionLevel = None

ProgressCallback = Callable[[str, str], None]
LOGGER = logging.getLogger("presence_bridge.gatt")
PAIRING_HANDOFF_GRACE_SECONDS = 300.0
PAIRING_COMPLETION_GRACE_SECONDS = 300.0
PAIRING_ATTEMPT_HARD_TIMEOUT_SECONDS = 300.0
MIN_PAIRING_RSSI_DBM = -82
MAX_ENCRYPTED_ACK_REJECTIONS = 3
MAX_POST_BOND_DISCOVERY_FAILURES = 2
MAX_INITIAL_DISCOVERY_FAILURES = 3
GATT_PAYLOAD_READ_TIMEOUT_SECONDS = 10.0


class GattMetadataLogFilter(logging.Filter):
    """Keep transport diagnostics without logging QR-authenticated payloads."""

    def filter(self, record: logging.LogRecord) -> bool:
        return not str(record.msg).startswith(
            ("Read Characteristic", "Read Descriptor", "Write Descriptor")
        )


class ReverseGattError(RuntimeError):
    """Pairing failure with a stable diagnostic code for Home Assistant."""

    terminal_state = "error"

    def __init__(self, message: str, detail_code: str) -> None:
        super().__init__(message)
        self.detail_code = detail_code


class ReverseGattTimeoutError(ReverseGattError):
    """The invitation or accepted attempt actually reached its deadline."""

    terminal_state = "timeout"


class SessionMismatchError(ReverseGattError):
    """The nearby app is advertising a different one-time invitation."""


class BondResetRequiredError(ReverseGattError):
    """A verified phone had a stale Windows bond that was reset for retry."""


class EncryptedAcknowledgementRejectedError(ReverseGattError):
    """A saved bond repeatedly failed the actual encrypted app exchange."""


class PairingDiagnosticError(ReverseGattError):
    """A one-shot pairing experiment must stop without normal-path retries."""


class ServiceDiscoveryBlockedError(ReverseGattError):
    """The current QR service cannot be opened after bounded clean attempts."""


@dataclass(frozen=True, slots=True)
class ReverseGattResult:
    """Verified peer information returned after the encrypted exchange."""

    address: str
    name: str
    transport: str = "iphone_peripheral"
    secure_exchange_complete: bool = False


@dataclass(frozen=True, slots=True)
class _ConnectionStrategy:
    """One bounded WinRT connection route."""

    label: str
    address_type: str | None
    filter_services: bool
    use_cached_services: bool | None
    pair_before_discovery: bool
    timeout: float


class ReverseGattPairingClient:
    """Use Windows as the BLE central and iPhone as the temporary peripheral."""

    def __init__(
        self,
        *,
        service_uuid: str,
        session_uuid: str,
        claim_uuid: str,
        result_uuid: str,
        progress_callback: ProgressCallback | None = None,
        pairing_probe: Callable[[Any, PairingLink, float], Awaitable[bool]]
        | None = None,
    ) -> None:
        self.service_uuid = service_uuid.lower()
        self.service_uuids = {self.service_uuid}
        self._session_service_uuid: str | None = None
        self._matched_service_uuid: str | None = None
        self._matched_address: str | None = None
        self._matched_rssi: int | None = None
        self.session_uuid = session_uuid.lower()
        self.claim_uuid = claim_uuid.lower()
        self.result_uuid = result_uuid.lower()
        self.progress_callback = progress_callback
        self.pairing_probe = pairing_probe
        self.detail_code = "waiting_for_iphone_advertisement"
        self._matched_advertisement: Any | None = None
        self._handoff_deadline: float | None = None
        self._handoff_expires_at: int | None = None
        self._completion_deadline: float | None = None
        self._completion_expires_at: int | None = None
        self._secure_bond_confirmed = False
        self._ack_auth_failures: dict[str, int] = {}
        self._authenticated_ack_addresses: set[str] = set()
        self._fresh_discovery_addresses: set[str] = set()
        self._initial_discovery_failures: dict[str, int] = {}
        self._qr_verified_addresses: set[str] = set()
        self._reused_bond_addresses: set[str] = set()
        self._created_bond_addresses: set[str] = set()
        self._bond_repair_used = False

    @property
    def lease_payload(self) -> dict[str, Any]:
        """Return non-secret deadlines and candidate metadata for the observer."""
        payload: dict[str, Any] = {}
        if self._handoff_expires_at is not None:
            payload["attempt_expires_at"] = self._handoff_expires_at
        if self._completion_expires_at is not None:
            payload["completion_expires_at"] = self._completion_expires_at
        if self._matched_address:
            payload["matched_address"] = self._matched_address
        if self._matched_rssi is not None:
            payload["rssi"] = self._matched_rssi
        if (
            self._session_service_uuid is not None
            and self._matched_service_uuid == self._session_service_uuid
        ):
            payload["session_scoped_advertisement"] = True
        return payload

    def _start_handoff_lease(self) -> None:
        if self._handoff_deadline is not None:
            return
        self._handoff_deadline = time.monotonic() + PAIRING_HANDOFF_GRACE_SECONDS
        self._handoff_expires_at = int(time.time() + PAIRING_HANDOFF_GRACE_SECONDS)

    def start_handoff_lease(self) -> None:
        """Keep a QR-started attempt alive while transport roles switch."""
        self._start_handoff_lease()

    def _start_completion_lease(self) -> None:
        if self._completion_deadline is not None:
            return
        self._start_handoff_lease()
        self._completion_deadline = self._handoff_deadline
        self._completion_expires_at = self._handoff_expires_at

    def _progress(self, detail_code: str, message: str) -> None:
        self.detail_code = detail_code
        if self.progress_callback is not None:
            self.progress_callback(detail_code, message)

    def _matches_advertisement(self, device: Any, advertisement: Any) -> bool:
        services = {
            str(value).lower()
            for value in (getattr(advertisement, "service_uuids", None) or [])
        }
        services.update(
            str(value).lower()
            for value in (getattr(advertisement, "service_data", None) or {})
        )
        matching_services = self.service_uuids.intersection(services)
        # A name-only match is unsafe and unreliable once services rotate per
        # QR session: Windows may retain an earlier "Presence Pair" advert and
        # connect to the wrong GATT database. Modern and legacy clients both
        # advertise an explicit service UUID, so only that UUID selects a peer.
        matched = bool(matching_services)
        if matched:
            self._matched_advertisement = advertisement
            try:
                self._matched_rssi = int(getattr(advertisement, "rssi", None))
            except (TypeError, ValueError):
                self._matched_rssi = None
            self._matched_service_uuid = (
                self._session_service_uuid
                if self._session_service_uuid in matching_services
                else next(iter(matching_services), None)
            )
        return matched

    @staticmethod
    def _windows_address_type(device: Any) -> str | None:
        """Return the WinRT address type carried by the matching advertisement."""
        details = getattr(device, "details", None)
        candidates = (
            getattr(details, "adv", None),
            getattr(details, "scan", None),
        )
        for candidate in candidates:
            value = getattr(candidate, "bluetooth_address_type", None)
            if value is None:
                continue
            label = str(getattr(value, "name", value)).casefold()
            if "random" in label:
                return "random"
            if "public" in label:
                return "public"

        # CoreBluetooth peripherals use a private random address. Older WinRT
        # advertisement wrappers do not expose the address type, so preserve a
        # working default for the only peripheral supported by this protocol.
        return "random" if sys.platform == "win32" else None

    @staticmethod
    def _error_summary(error: BaseException) -> str:
        detail = str(error).strip()
        if "Could not write value" in detail:
            _, separator, target = detail.rpartition(" to characteristic ")
            detail = "Protected GATT write failed (payload omitted)"
            if separator:
                detail += f"; characteristic {target}"
        winerror = getattr(error, "winerror", None)
        suffix = f" (Windows {winerror})" if winerror is not None else ""
        return f"{type(error).__name__}{suffix}: {detail}".rstrip(": ")[:180]

    @staticmethod
    def _is_resettable_bond_error(error: BaseException) -> bool:
        """Identify WinRT failures that leave or reuse an unusable BLE bond."""
        if isinstance(error, BleakGATTProtocolError) and int(error.code) == 0xF2:
            return True
        return (
            isinstance(error, BleakError)
            and "could not pair with device" in str(error).casefold()
        )

    @classmethod
    def _is_stale_secure_bond_error(cls, error: BaseException) -> bool:
        """Identify a bond that still cannot authorize protected GATT data."""
        if isinstance(error, BleakGATTProtocolError) and int(error.code) == 0x05:
            return True
        return cls._is_resettable_bond_error(error)

    def _log_detected_candidate(self, device: Any) -> None:
        """Record enough radio metadata to diagnose WinRT without QR secrets."""
        advertisement = self._matched_advertisement
        self._matched_address = str(getattr(device, "address", "") or "") or None
        LOGGER.info(
            "Presence Pair advertisement detected "
            "(address=%s, address_type=%s, name=%r, rssi=%s, tx_power=%s)",
            str(getattr(device, "address", "") or "unknown"),
            self._windows_address_type(device) or "automatic",
            str(
                getattr(advertisement, "local_name", None)
                or getattr(device, "name", None)
                or "Presence Pair"
            ),
            getattr(advertisement, "rssi", "unknown"),
            getattr(advertisement, "tx_power", "unknown"),
        )

    async def _unpair_candidate(
        self,
        device: Any,
        *,
        address_type: str | None,
    ) -> None:
        """Remove one advertised Presence Pair device from the Windows bond cache."""
        winrt: dict[str, Any] = {"use_cached_services": False}
        if address_type is not None:
            winrt["address_type"] = address_type
        client = BleakClient(device, timeout=15, winrt=winrt)
        await asyncio.wait_for(client.unpair(), timeout=15)

    async def _remove_verified_peer_bonds(self, device: Any, client: Any) -> None:
        """Remove both transports of the exact verified Windows phone only."""
        requester = getattr(getattr(client, "_backend", None), "_requester", None)
        native_address = getattr(requester, "bluetooth_address", None)
        if sys.platform == "win32" and (
            client is None or isinstance(native_address, int)
        ):
            addresses = {compact_address(str(getattr(device, "address", "") or ""))}
            if isinstance(native_address, int):
                addresses.add(f"{native_address:012X}")
            addresses.discard("")
            inventory = await list_bonded_devices()
            # Container expansion starts from a BLE endpoint, not from a name
            # or a coincidentally similar classic Bluetooth address.
            direct = [
                d for d in inventory if d.transport == "ble" and d.address in addresses
            ]
            targets = select_bond_devices(
                [{"registry_leaf": d.address} for d in direct], inventory
            )
            if not targets:
                raise RuntimeError(
                    "The verified phone has no identifiable saved BLE bond"
                )
            other_ids = {d.device_id for d in inventory if d not in targets}
            if client is not None:
                await self._release_client(client)
            for target in targets:
                await unpair_device(target)
            remaining = await list_bonded_devices()
            remaining_ids = {d.device_id for d in remaining}
            target_ids = {d.device_id for d in targets}
            if target_ids & remaining_ids or not other_ids.issubset(remaining_ids):
                raise RuntimeError("Saved-bond removal could not be verified")
        elif client is not None:
            await client.unpair()
        else:
            await self._unpair_candidate(
                device, address_type=self._windows_address_type(device)
            )

    async def _repair_verified_bond(
        self, device: Any, client: Any, deadline: float
    ) -> None:
        """Repair once per QR attempt, never from a timeout or advertisement alone."""
        address = str(getattr(device, "address", "") or "").casefold()
        if address not in self._qr_verified_addresses or self._bond_repair_used:
            raise PairingDiagnosticError(
                "Automatic bond repair is not authorized for this peer or was already used",
                "iphone_bond_repair_unavailable",
            )
        remaining = deadline - time.monotonic()
        if remaining <= 5:
            raise ReverseGattTimeoutError(
                "No time remains for safe bond recovery", "iphone_bond_repair_timeout"
            )
        self._bond_repair_used = True
        self._progress(
            "iphone_bond_repairing",
            "Repairing only this QR-verified phone's saved pairing",
        )
        try:
            await asyncio.wait_for(
                self._remove_verified_peer_bonds(device, client),
                timeout=min(15, remaining),
            )
        except asyncio.CancelledError:
            raise
        except Exception as error:
            raise PairingDiagnosticError(
                "The receiver could not verify removal of this phone's old bond; recovery stopped",
                "iphone_bond_reset_failed",
            ) from error
        self._secure_bond_confirmed = False
        self._authenticated_ack_addresses.discard(address)
        self._reused_bond_addresses.discard(address)
        self._fresh_discovery_addresses.add(address)
        self._initial_discovery_failures.pop(address, None)
        self._ack_auth_failures.pop(address, None)
        raise BondResetRequiredError(
            "The old receiver-side bond was removed for this verified phone only. "
            "Reconnecting now; accept Pair on the iPhone if requested.",
            "iphone_bond_reset",
        )

    async def _connect_candidate(
        self,
        device: Any,
        *,
        connection_timeout: float,
        address_type: str | None,
        filter_services: bool,
        use_cached_services: bool | None,
        pair_before_discovery: bool,
    ) -> BleakClient:
        winrt: dict[str, Any] = {}
        if address_type is not None:
            winrt["address_type"] = address_type
        if use_cached_services is not None:
            winrt["use_cached_services"] = use_cached_services
        kwargs: dict[str, Any] = {
            "timeout": connection_timeout,
            "winrt": winrt,
            "pair": pair_before_discovery,
        }
        if filter_services:
            kwargs["services"] = [self._matched_service_uuid or self.service_uuid]
        client = BleakClient(device, **kwargs)
        self._disable_unowned_service_change_retry(client)
        try:
            await asyncio.wait_for(
                client.connect(),
                timeout=connection_timeout,
            )
            return client
        except BaseException:
            await self._release_client(client)
            raise

    @staticmethod
    async def _release_client(client: BleakClient) -> None:
        """Stop WinRT reconnects even when the last GATT status was CLOSED."""
        if sys.platform == "win32":
            session = getattr(getattr(client, "_backend", None), "_session", None)
            if session is not None:
                with suppress(Exception):
                    session.maintain_connection = False
        with suppress(Exception):
            await asyncio.wait_for(client.disconnect(), timeout=5)

    @staticmethod
    def _disable_unowned_service_change_retry(client: BleakClient) -> None:
        """Keep discovery cancellation owned by the connect coroutine."""
        if sys.platform != "win32":
            return
        backend = getattr(client, "_backend", None)
        if backend is None or not hasattr(backend, "_retry_on_services_changed"):
            return
        # Bleak's optional retry loop creates discovery/wait tasks that survive
        # cancellation of connect(). They can overlap the next route or unpair.
        # Use its default single awaited request; retry owned routes below.
        backend._retry_on_services_changed = False

    @staticmethod
    async def _windows_reports_paired(client: BleakClient) -> bool:
        """Confirm a bond that WinRT committed despite returning an error."""
        if WinRTDeviceInformation is None:
            return False
        backend = getattr(client, "_backend", None)
        requester = getattr(backend, "_requester", None)
        device_information = getattr(requester, "device_information", None)
        device_id = getattr(device_information, "id", None)
        if not device_id:
            return False
        try:
            refreshed = await WinRTDeviceInformation.create_from_id_async(device_id)
            return bool(refreshed.pairing.is_paired)
        except Exception as error:
            LOGGER.debug(
                "Unable to refresh the Windows pairing state: %s",
                ReverseGattPairingClient._error_summary(error),
            )
            return False

    @staticmethod
    def _gatt_inventory(client: BleakClient) -> str:
        """Return a compact, non-secret view of discovered GATT UUIDs."""
        inventory: list[str] = []
        try:
            for service in client.services:
                characteristics = ",".join(
                    str(characteristic.uuid).lower()
                    for characteristic in service.characteristics
                )
                inventory.append(f"{str(service.uuid).lower()}=[{characteristics}]")
        except Exception as error:
            return f"unavailable:{type(error).__name__}"
        return ";".join(inventory) or "empty"

    @staticmethod
    async def _candidate_has_saved_bond(device: Any) -> bool | None:
        """Read only the candidate's saved-bond status; never connect or unpair."""
        if sys.platform != "win32":
            return None
        address = compact_address(str(getattr(device, "address", "") or ""))
        if not address:
            return None
        try:
            devices = await asyncio.wait_for(list_bonded_devices(), timeout=3)
        except asyncio.CancelledError:
            raise
        except Exception as error:
            LOGGER.info("Saved-bond inspection unavailable: %s", type(error).__name__)
            return None
        return any(row.address == address and row.transport == "ble" for row in devices)

    async def _open_candidate(
        self,
        device: Any,
        time_budget: float,
    ) -> BleakClient:
        """Try discovery, then let the caller repair only the QR-selected peer."""
        deadline = time.monotonic() + min(82.0, time_budget)
        address = str(getattr(device, "address", "") or "").casefold()
        detected_type = self._windows_address_type(device)
        strategies: list[_ConnectionStrategy] = []
        if self._secure_bond_confirmed:
            strategies.append(
                _ConnectionStrategy(
                    "paired encrypted service discovery",
                    detected_type,
                    True,
                    False,
                    True,
                    18.0,
                )
            )
        strategies.extend(
            [
                _ConnectionStrategy(
                    "native filtered service discovery",
                    detected_type,
                    True,
                    None,
                    False,
                    14.0,
                ),
                _ConnectionStrategy(
                    "fresh full service discovery",
                    detected_type,
                    False,
                    False,
                    False,
                    18.0,
                ),
                _ConnectionStrategy(
                    "fresh filtered service discovery",
                    detected_type,
                    True,
                    False,
                    False,
                    14.0,
                ),
                _ConnectionStrategy(
                    "cached full service discovery",
                    detected_type,
                    False,
                    True,
                    False,
                    10.0,
                ),
            ]
        )
        if detected_type is not None:
            strategies.append(
                _ConnectionStrategy(
                    "automatic-address discovery fallback",
                    None,
                    False,
                    False,
                    False,
                    18.0,
                )
            )

        if self._session_service_uuid is not None and (
            self._matched_service_uuid == self._session_service_uuid
        ):
            # The invitation selects one service. Enumerating every Apple/old
            # app service adds unrelated requests and stale-cache failures.
            fresh = _ConnectionStrategy(
                "fresh QR service discovery", detected_type, True, False, False, 10.0
            )
            cached = _ConnectionStrategy(
                "refreshed QR service discovery", detected_type, True, True, False, 6.0
            )
            # After pairing, reuse the services Windows just discovered first.
            # Never ask for pairing again before verifying the current QR.
            if address in self._fresh_discovery_addresses:
                # A cached UUID list is not proof that its characteristic handles
                # still work after bonding. Never retry known-unreadable handles.
                strategies = [fresh]
            else:
                strategies = (
                    [cached, fresh] if self._secure_bond_confirmed else [fresh, cached]
                )
            if self._initial_discovery_failures.get(address) == 1:
                # Some Windows caches cannot filter a newly published iOS UUID.
                # One complete discovery preserves the bond and still requires
                # the exact session characteristic, QR claim and encrypted ACK.
                strategies.append(
                    _ConnectionStrategy(
                        "uncached complete QR service lookup",
                        detected_type,
                        False,
                        False,
                        False,
                        12.0,
                    )
                )

        last_error: BaseException | None = None
        for index, strategy in enumerate(strategies):
            remaining = deadline - time.monotonic()
            if remaining <= 3:
                break
            attempt_timeout = min(strategy.timeout, max(3.0, remaining - 1.0))
            try:
                connected = await self._connect_candidate(
                    device,
                    connection_timeout=attempt_timeout,
                    address_type=strategy.address_type,
                    filter_services=strategy.filter_services,
                    use_cached_services=strategy.use_cached_services,
                    pair_before_discovery=strategy.pair_before_discovery,
                )
                inventory = self._gatt_inventory(connected)
                if self.session_uuid in inventory:
                    self._initial_discovery_failures.pop(address, None)
                    return connected
                LOGGER.warning(
                    "iPhone GATT connection opened without the Presence Pair "
                    "session (%s): %s",
                    strategy.label,
                    inventory,
                )
                await self._release_client(connected)
                raise BleakCharacteristicNotFoundError(self.session_uuid)
            except asyncio.CancelledError:
                raise
            except BaseException as error:
                last_error = error
                LOGGER.warning(
                    "iPhone GATT connection strategy failed "
                    "(%s, address_type=%s, cache=%s, pre_pair=%s): %s",
                    strategy.label,
                    strategy.address_type or "automatic",
                    (
                        "native"
                        if strategy.use_cached_services is None
                        else str(strategy.use_cached_services).lower()
                    ),
                    str(strategy.pair_before_discovery).lower(),
                    self._error_summary(error),
                )
                if index + 1 < len(strategies):
                    self._progress(
                        "iphone_connection_retry",
                        "Windows could not finish this Bluetooth route; trying the next compatible route",
                    )
                    await asyncio.sleep(0.35)

        if last_error is None:
            raise TimeoutError("No time remained for a Bluetooth connection")
        if (
            not self._secure_bond_confirmed
            and self._session_service_uuid is not None
            and self._matched_service_uuid == self._session_service_uuid
        ):
            failures = self._initial_discovery_failures.get(address, 0) + 1
            self._initial_discovery_failures[address] = failures
            if failures >= MAX_INITIAL_DISCOVERY_FAILURES:
                saved_bond = await self._candidate_has_saved_bond(device)
                LOGGER.warning(
                    "QR service discovery blocked: attempts=%d saved_bond=%s last_error=%s",
                    failures,
                    saved_bond,
                    self._error_summary(last_error),
                )
                if saved_bond:
                    raise ServiceDiscoveryBlockedError(
                        "The receiver still remembers this iPhone, but cannot open its "
                        "current app service. If the iPhone forgot the receiver, remove "
                        "its saved receiver-side pairing too before trying again. No "
                        "bond was erased and enrollment did not complete.",
                        "iphone_saved_bond_unreachable",
                    ) from last_error
                raise ServiceDiscoveryBlockedError(
                    "The iPhone is advertising this QR session, but its app service "
                    "cannot be opened after three attempts. Keep Presence Pair open "
                    "near the receiver and start a new attempt. No device was paired "
                    "or removed.",
                    "iphone_service_unreachable",
                ) from last_error
        raise last_error

    async def _read_phone_payload(
        self, client: Any, device: Any, characteristic_uuid: str
    ) -> bytes | bytearray:
        try:
            return await asyncio.wait_for(
                client.read_gatt_char(characteristic_uuid),
                timeout=GATT_PAYLOAD_READ_TIMEOUT_SECONDS,
            )
        except asyncio.CancelledError:
            raise
        except (BleakError, OSError, TimeoutError):
            address = str(getattr(device, "address", "") or "").casefold()
            self._fresh_discovery_addresses.add(address)
            self._progress(
                "iphone_services_refresh",
                "The iPhone service stopped responding; retrying with a fresh "
                "service lookup while preserving the Bluetooth pairing",
            )
            raise

    @staticmethod
    def _decode_json(value: bytes | bytearray, label: str) -> dict[str, Any]:
        try:
            payload = json.loads(bytes(value).decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as err:
            raise ReverseGattError(
                f"The iPhone returned an invalid {label} payload",
                f"iphone_{label}_invalid",
            ) from err
        if not isinstance(payload, dict):
            raise ReverseGattError(
                f"The iPhone returned an invalid {label} payload",
                f"iphone_{label}_invalid",
            )
        return payload

    @staticmethod
    def _session_matches(link: PairingLink, payload: dict[str, Any]) -> bool:
        try:
            return (
                int(payload.get("v")) == link.version
                and str(payload.get("sid") or "") == link.session_id
                and str(payload.get("oid") or "") == link.observer_id
                and int(payload.get("exp")) == link.expires_at
            )
        except (TypeError, ValueError):
            return False

    def _require_ack_encryption(
        self, client: Any, *, require_authentication: bool = False
    ) -> None:
        """Ask WinRT to secure this GATT link, not merely save a pairing record."""
        if WinRTGattProtectionLevel is None:
            return
        characteristic = client.services.get_characteristic(self.result_uuid)
        if characteristic is None:
            raise BleakCharacteristicNotFoundError(self.result_uuid)
        native = characteristic.obj
        try:
            previous = native.protection_level
            requested = WinRTGattProtectionLevel.ENCRYPTION_REQUIRED
            if require_authentication or previous in {
                WinRTGattProtectionLevel.AUTHENTICATION_REQUIRED,
                WinRTGattProtectionLevel.ENCRYPTION_AND_AUTHENTICATION_REQUIRED,
            }:
                requested = (
                    WinRTGattProtectionLevel.ENCRYPTION_AND_AUTHENTICATION_REQUIRED
                )
            native.protection_level = requested
            if native.protection_level != requested:
                raise RuntimeError("Windows ignored the encryption requirement")
        except Exception as error:
            raise EncryptedAcknowledgementRejectedError(
                "Windows could not require encryption for the iPhone confirmation",
                "iphone_encryption_failed",
            ) from error
        LOGGER.info(
            "iPhone acknowledgement protection requested: previous=%s requested=%s",
            previous,
            requested,
        )

    async def _retry_ack_with_authentication(
        self,
        client: Any,
        device: Any,
        acknowledgement: bytes,
        error: BaseException | None,
        deadline: float,
    ) -> BaseException | None:
        """A live encrypted link can still need authenticated pairing for ATT 0x05."""
        address = str(getattr(device, "address", "") or "").casefold()
        if (
            WinRTGattProtectionLevel is None
            or not isinstance(error, BleakGATTProtocolError)
            or error.code != 0x05
            or address in self._authenticated_ack_addresses
            or deadline <= time.monotonic()
        ):
            return error
        self._authenticated_ack_addresses.add(address)
        self._require_ack_encryption(client, require_authentication=True)
        self._progress(
            "iphone_authentication_required",
            "iPhone requires stronger Bluetooth authentication; confirm any system pairing request",
        )
        try:
            await asyncio.wait_for(
                client.write_gatt_char(
                    self.result_uuid, acknowledgement, response=True
                ),
                timeout=min(20.0, max(0.0, deadline - time.monotonic())),
            )
        except asyncio.CancelledError:
            raise
        except Exception as stronger_error:
            LOGGER.warning(
                "Authenticated encrypted acknowledgement failed: %s",
                self._error_summary(stronger_error),
            )
            raise EncryptedAcknowledgementRejectedError(
                "The iPhone rejected the protected confirmation and Windows could "
                "not establish the required authentication. This attempt has stopped; "
                "the saved Bluetooth bond was preserved.",
                "iphone_encryption_failed",
            ) from stronger_error
        self._progress(
            "iphone_claim_accepted",
            "Authenticated encrypted app claim accepted; capturing the private identity",
        )
        return None

    def _record_ack_rejection(self, device: Any, error: BaseException | None) -> None:
        if not isinstance(error, BleakGATTProtocolError) or error.code not in {
            0x05,
            0x0F,
        }:
            return
        address = str(getattr(device, "address", "") or "").casefold()
        count = self._ack_auth_failures.get(address, 0) + 1
        self._ack_auth_failures[address] = count
        LOGGER.warning(
            "Encrypted iPhone acknowledgement rejected after Windows saved the bond: "
            "ATT=0x%02x attempt=%s/%s",
            error.code,
            count,
            MAX_ENCRYPTED_ACK_REJECTIONS,
        )
        if count >= MAX_ENCRYPTED_ACK_REJECTIONS:
            raise EncryptedAcknowledgementRejectedError(
                "Windows saved the Bluetooth pairing, but the iPhone rejected the "
                "encrypted confirmation three times. Pairing was not completed. "
                "Keep the app open and retry with a new code; do not keep waiting "
                "on this attempt.",
                "iphone_encryption_failed",
            ) from error

    async def _pair_candidate(
        self,
        device: Any,
        link: PairingLink,
        time_budget: float,
        *,
        allow_bond_reset: bool,
    ) -> ReverseGattResult:
        deadline = time.monotonic() + max(3.0, time_budget)
        self._progress(
            "iphone_advertisement_seen",
            "iPhone found; opening the local Bluetooth connection",
        )
        # Stopping a WinRT watcher and opening a GATT session in the same event
        # loop tick is unreliable on a few Windows Bluetooth drivers.
        await asyncio.sleep(0.2)
        client = await self._open_candidate(
            device,
            max(3.0, deadline - time.monotonic()),
        )
        try:
            self._progress(
                "iphone_connected",
                "iPhone connected; checking the active QR session",
            )
            try:
                session_value = await self._read_phone_payload(
                    client, device, self.session_uuid
                )
            except (BleakCharacteristicNotFoundError, BleakGATTProtocolError):
                inventory = self._gatt_inventory(client)
                LOGGER.warning(
                    "Presence Pair GATT session unavailable after connect: %s",
                    inventory,
                )
                raise
            session = self._decode_json(session_value, "session")
            if not self._session_matches(link, session):
                raise SessionMismatchError(
                    "A nearby iPhone is using a different or expired QR code",
                    "iphone_session_mismatch",
                )

            self._start_completion_lease()
            if self._completion_deadline is not None:
                deadline = max(deadline, self._completion_deadline)
            self._progress(
                "iphone_session_verified",
                "QR session verified; the active pairing now has its own completion window",
            )
            claim_value = await self._read_phone_payload(
                client, device, self.claim_uuid
            )
            claim = self._decode_json(claim_value, "claim")
            if not self._session_matches(link, claim) or not verify_claim(
                link,
                str(claim.get("proof") or ""),
                allow_expired=True,
            ):
                raise ReverseGattError(
                    "The iPhone did not prove possession of the active QR code",
                    "iphone_claim_rejected",
                )
            self._progress(
                "iphone_claim_received",
                "Pairing code verified; waiting for the iPhone Bluetooth confirmation",
            )
            address = str(getattr(device, "address", "") or "").casefold()
            self._qr_verified_addresses.add(address)

            acknowledgement = json.dumps(
                {
                    "v": link.version,
                    "sid": link.session_id,
                    "oid": link.observer_id,
                    "exp": link.expires_at,
                    "status": "accepted",
                    "proof": acceptance_proof(link),
                },
                separators=(",", ":"),
                sort_keys=True,
            ).encode("utf-8")

            # The Windows policy pairs before the protected write, only after
            # the session and HMAC claim have both been verified above.
            diagnostic_pairing = False
            if self.pairing_probe is not None:
                try:
                    diagnostic_pairing = await self.pairing_probe(
                        client, link, deadline
                    )
                except PairingDiagnosticError as error:
                    if (
                        error.detail_code == "numeric_pairing_saved_bond_weak"
                        and getattr(self.pairing_probe, "allow_bond_repair", False)
                        is True
                        and allow_bond_reset
                        and not self._bond_repair_used
                    ):
                        await self._repair_verified_bond(device, client, deadline)
                    raise
            if diagnostic_pairing:
                self._secure_bond_confirmed = True
                if (
                    getattr(self.pairing_probe, "reused_bond", False) is True
                    and address not in self._created_bond_addresses
                ):
                    self._reused_bond_addresses.add(address)
                    self._progress(
                        "iphone_existing_bond_verified",
                        "Reusing the saved pairing; checking the encrypted app exchange",
                    )
                elif getattr(self.pairing_probe, "reused_bond", None) is False:
                    self._created_bond_addresses.add(address)
                self._authenticated_ack_addresses.add(
                    str(getattr(device, "address", "") or "").casefold()
                )

            # The iPhone requests bonding when this encryption-protected
            # characteristic is accessed. Trigger that request before asking
            # WinRT to pair explicitly; otherwise iOS may never show its Pair
            # prompt and Bleak waits until timeout with no visible action.
            self._require_ack_encryption(
                client,
                require_authentication=(
                    str(getattr(device, "address", "") or "").casefold()
                    in self._authenticated_ack_addresses
                ),
            )
            acknowledgement_error: BaseException | None = None
            try:
                remaining = max(3.0, deadline - time.monotonic())
                await asyncio.wait_for(
                    client.write_gatt_char(
                        self.result_uuid,
                        acknowledgement,
                        response=True,
                    ),
                    timeout=min(35.0, remaining),
                )
            except asyncio.CancelledError:
                raise
            except BaseException as error:
                if diagnostic_pairing:
                    if (
                        allow_bond_reset
                        and getattr(self.pairing_probe, "reused_bond", False) is True
                        and address not in self._created_bond_addresses
                        and isinstance(error, BleakGATTProtocolError)
                        and error.code in {0x05, 0x0F}
                    ):
                        # Repair only an old bond whose exact QR-authenticated
                        # peer rejected encryption. Timeouts never justify this.
                        await self._repair_verified_bond(device, client, deadline)
                    if (
                        getattr(self.pairing_probe, "allow_link_recovery", False)
                        is True
                        and getattr(client, "is_connected", None) is False
                        and isinstance(error, BleakError | OSError)
                        and not isinstance(error, BleakGATTProtocolError)
                        and time.monotonic() < deadline
                    ):
                        # WinRT can close the GATT link immediately after a
                        # successful bond. Reopen it, not the pairing ceremony.
                        # The outer loop bounds retries and revalidates QR,
                        # authentication and the protected write on every route.
                        address = str(getattr(device, "address", "") or "").casefold()
                        self._fresh_discovery_addresses.add(address)
                        self._progress(
                            "iphone_bond_reconnecting",
                            "Bluetooth pairing was accepted; the connection closed "
                            "during confirmation. Reconnecting without removing "
                            "the saved bond or restarting the timer",
                        )
                        raise ReverseGattError(
                            "Authenticated bond preserved after a closed confirmation "
                            f"channel ({self._error_summary(error)})",
                            "iphone_bond_reconnecting",
                        ) from error
                    raise PairingDiagnosticError(
                        "Numeric comparison completed, but the protected app "
                        f"acknowledgement failed: {self._error_summary(error)}",
                        "numeric_comparison_ack_failed",
                    ) from error
                acknowledgement_error = error
                LOGGER.info(
                    "Protected iPhone acknowledgement requires pairing fallback: %s",
                    self._error_summary(error),
                )
            else:
                self._progress(
                    "iphone_claim_accepted",
                    "Encrypted app claim accepted; capturing the private identity",
                )
                return ReverseGattResult(
                    address=str(getattr(device, "address", "") or ""),
                    name=str(getattr(device, "name", "") or "Presence Pair iPhone"),
                    secure_exchange_complete=True,
                )

            if await self._windows_reports_paired(client):
                self._secure_bond_confirmed = True
                acknowledgement_error = await self._retry_ack_with_authentication(
                    client, device, acknowledgement, acknowledgement_error, deadline
                )
                if acknowledgement_error is None:
                    return ReverseGattResult(
                        address=str(getattr(device, "address", "") or ""),
                        name=str(getattr(device, "name", "") or "Presence Pair iPhone"),
                        secure_exchange_complete=True,
                    )
                self._record_ack_rejection(device, acknowledgement_error)
                self._progress(
                    "iphone_bond_reconnecting",
                    "Bluetooth bond accepted; reconnecting to confirm completion on the iPhone",
                )
                raise ReverseGattError(
                    "The Bluetooth bond is ready; reconnecting to acknowledge the app",
                    "iphone_bond_reconnecting",
                )

            try:
                remaining = max(3.0, deadline - time.monotonic())
                await asyncio.wait_for(client.pair(), timeout=min(30.0, remaining))
                self._secure_bond_confirmed = True
            except asyncio.CancelledError:
                raise
            except Exception as error:
                if await self._windows_reports_paired(client):
                    self._secure_bond_confirmed = True
                    LOGGER.warning(
                        "WinRT reported a pairing error after committing the bond: %s",
                        self._error_summary(error),
                    )
                elif allow_bond_reset and self._is_stale_secure_bond_error(error):
                    await self._repair_verified_bond(device, client, deadline)
                else:
                    raise

            self._progress(
                "iphone_bond_settling",
                "Secure Bluetooth bond accepted; waiting for the encrypted link to settle",
            )
            await asyncio.sleep(1.25)

            if not client.is_connected:
                self._progress(
                    "iphone_bond_reconnecting",
                    "Bluetooth bond accepted; reconnecting to confirm completion on the iPhone",
                )
                raise ReverseGattError(
                    "The Bluetooth bond is ready; reconnecting to acknowledge the app",
                    "iphone_bond_reconnecting",
                )

            try:
                remaining = max(3.0, deadline - time.monotonic())
                await asyncio.wait_for(
                    client.write_gatt_char(
                        self.result_uuid,
                        acknowledgement,
                        response=True,
                    ),
                    timeout=min(20.0, remaining),
                )
            except asyncio.CancelledError:
                raise
            except Exception as error:
                LOGGER.warning(
                    "The iPhone acknowledgement failed after a successful bond: %s",
                    self._error_summary(error),
                )
                error = await self._retry_ack_with_authentication(
                    client, device, acknowledgement, error, deadline
                )
                if error is None:
                    return ReverseGattResult(
                        address=str(getattr(device, "address", "") or ""),
                        name=str(getattr(device, "name", "") or "Presence Pair iPhone"),
                        secure_exchange_complete=True,
                    )
                self._record_ack_rejection(device, error)
                self._progress(
                    "iphone_bond_reconnecting",
                    "Bluetooth bond accepted; reconnecting to confirm completion on the iPhone",
                )
                raise ReverseGattError(
                    "The Bluetooth bond is ready; reconnecting to acknowledge the app",
                    "iphone_bond_reconnecting",
                ) from error
            else:
                self._progress(
                    "iphone_claim_accepted",
                    "Encrypted app claim accepted; capturing the private identity",
                )
            return ReverseGattResult(
                address=str(getattr(device, "address", "") or ""),
                name=str(getattr(device, "name", "") or "Presence Pair iPhone"),
                secure_exchange_complete=True,
            )
        finally:
            await self._release_client(client)

    async def async_pair(
        self,
        link: PairingLink,
        timeout_seconds: int,
    ) -> ReverseGattResult:
        """Wait for the matching iPhone, verify its claim, and create a bond."""
        link.validate()
        self._session_service_uuid = pairing_service_uuid(link)
        self.service_uuids = {
            self.service_uuid,
            self._session_service_uuid,
        }
        self._matched_service_uuid = None
        self._matched_address = None
        self._matched_rssi = None
        self._handoff_deadline = None
        self._handoff_expires_at = None
        self._completion_deadline = None
        self._completion_expires_at = None
        self._secure_bond_confirmed = False
        self._ack_auth_failures = {}
        self._authenticated_ack_addresses = set()
        self._fresh_discovery_addresses = set()
        self._initial_discovery_failures = {}
        self._qr_verified_addresses = set()
        self._reused_bond_addresses = set()
        self._created_bond_addresses = set()
        self._bond_repair_used = False
        invitation_deadline = min(
            time.monotonic() + timeout_seconds,
            time.monotonic() + max(1, link.expires_at - int(time.time())),
        )
        last_error: Exception | None = None
        reset_bond_addresses: set[str] = set()
        post_bond_discovery_failures = 0
        post_bond_write_failures: dict[str, int] = {}
        self._progress(
            "waiting_for_iphone_advertisement",
            "Receiver ready; scan the QR code and keep Presence Pair open",
        )
        while True:
            active_deadline = (
                self._completion_deadline
                or self._handoff_deadline
                or invitation_deadline
            )
            remaining = active_deadline - time.monotonic()
            if remaining <= 0:
                break
            try:
                device = await BleakScanner.find_device_by_filter(
                    self._matches_advertisement,
                    timeout=min(8.0, remaining),
                )
            except BleakBluetoothNotAvailableError as err:
                last_error = err
                self._progress(
                    "windows_adapter_recovering",
                    "Windows is reopening the Bluetooth adapter",
                )
                await asyncio.sleep(min(2.0, max(0.1, remaining)))
                continue
            if device is None:
                continue
            # Once the receiver has seen the exact one-time service UUID, QR
            # expiry must not interrupt an in-flight local BLE connection.
            self._start_handoff_lease()
            self._log_detected_candidate(device)
            device_address = str(getattr(device, "address", "") or "").casefold()
            if (
                self._matched_rssi is not None
                and self._matched_rssi < MIN_PAIRING_RSSI_DBM
            ):
                message = (
                    f"iPhone found at {self._matched_rssi} dBm; move it closer to "
                    "the Dell for this one-time secure pairing"
                )
                last_error = ReverseGattError(message, "iphone_signal_too_weak")
                self._progress("iphone_signal_too_weak", message)
                await asyncio.sleep(1.0)
                continue
            attempt_deadline = (
                self._completion_deadline
                or self._handoff_deadline
                or invitation_deadline
            )
            try:
                return await asyncio.wait_for(
                    self._pair_candidate(
                        device,
                        link,
                        max(
                            3.0,
                            attempt_deadline - time.monotonic(),
                        ),
                        allow_bond_reset=(
                            not self._bond_repair_used
                            and device_address not in reset_bond_addresses
                        ),
                    ),
                    timeout=min(
                        PAIRING_ATTEMPT_HARD_TIMEOUT_SECONDS,
                        max(0.1, attempt_deadline - time.monotonic()),
                    ),
                )
            except BondResetRequiredError as err:
                reset_bond_addresses.add(device_address)
                post_bond_discovery_failures = 0
                post_bond_write_failures.clear()
                last_error = err
                self._progress(err.detail_code, str(err))
            except SessionMismatchError as err:
                last_error = err
                self._progress(err.detail_code, str(err))
            except (
                EncryptedAcknowledgementRejectedError,
                PairingDiagnosticError,
                ServiceDiscoveryBlockedError,
            ) as err:
                self._progress(err.detail_code, str(err))
                raise
            except asyncio.CancelledError:
                raise
            except Exception as err:
                # Missing services or a timeout do not prove a corrupt bond.
                # Only the verified protected-exchange path may repair one.
                failed_recovery = (
                    device_address in self._reused_bond_addresses
                    and device_address in self._qr_verified_addresses
                    and not self._bond_repair_used
                    and (
                        post_bond_write_failures.get(device_address, 0) + 1
                        >= MAX_ENCRYPTED_ACK_REJECTIONS
                        if getattr(err, "detail_code", None)
                        == "iphone_bond_reconnecting"
                        else post_bond_discovery_failures + 1
                        >= MAX_POST_BOND_DISCOVERY_FAILURES
                    )
                )
                if failed_recovery:
                    try:
                        await self._repair_verified_bond(device, None, attempt_deadline)
                    except BondResetRequiredError as repaired:
                        reset_bond_addresses.add(device_address)
                        post_bond_discovery_failures = 0
                        post_bond_write_failures.clear()
                        last_error = repaired
                        self._progress(repaired.detail_code, str(repaired))
                        continue
                if (
                    self._secure_bond_confirmed
                    and getattr(err, "detail_code", None) == "iphone_bond_reconnecting"
                ):
                    count = post_bond_write_failures.get(device_address, 0) + 1
                    post_bond_write_failures[device_address] = count
                    if count >= MAX_ENCRYPTED_ACK_REJECTIONS:
                        failure = ReverseGattError(
                            "The saved Bluetooth bond could not confirm the app after "
                            "three protected-write recovery attempts. Pairing stopped "
                            "without erasing the bond or adding an identity.",
                            "iphone_bond_link_failed",
                        )
                        self._progress(failure.detail_code, str(failure))
                        raise failure from err
                if (
                    self._secure_bond_confirmed
                    and getattr(err, "detail_code", None) != "iphone_bond_reconnecting"
                ):
                    post_bond_discovery_failures += 1
                    if post_bond_discovery_failures >= MAX_POST_BOND_DISCOVERY_FAILURES:
                        failure = ReverseGattError(
                            "Windows saved the pairing but could not reopen the "
                            "iPhone service after two clean recovery attempts. "
                            "The connection is not complete; the saved bond was preserved.",
                            "iphone_bond_link_failed",
                        )
                        self._progress(failure.detail_code, str(failure))
                        raise failure from err
                last_error = err
                LOGGER.warning(
                    "iPhone pairing attempt failed: %s",
                    self._error_summary(err),
                )
                self._progress(
                    getattr(err, "detail_code", "iphone_connection_failed"),
                    f"iPhone connection failed ({self._error_summary(err)}); retrying",
                )
            active_deadline = (
                self._completion_deadline
                or self._handoff_deadline
                or invitation_deadline
            )
            await asyncio.sleep(min(1.0, max(0.0, active_deadline - time.monotonic())))

        detail = getattr(last_error, "detail_code", self.detail_code)
        if self._completion_deadline is not None:
            message = (
                "The QR was accepted in time, but secure pairing did not finish "
                "within the five-minute pairing window"
            )
        elif self._handoff_deadline is not None:
            message = (
                "The iPhone was found before the code expired, but its QR session "
                "could not be verified within the connection window"
            )
        else:
            message = "The receiver did not find the iPhone before the code expired"
        if last_error is not None:
            message += f". Last step: {self._error_summary(last_error)}"
        raise ReverseGattTimeoutError(message, detail) from last_error
