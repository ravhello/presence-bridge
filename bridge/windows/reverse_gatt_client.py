"""Connect to the short-lived GATT service advertised by Presence Pair."""

from __future__ import annotations

import asyncio
import json
import logging
import sys
import time
from collections.abc import Callable
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
from protocol import PairingLink, acceptance_proof, verify_claim

ProgressCallback = Callable[[str, str], None]
LOGGER = logging.getLogger("presence_bridge.gatt")
PAIRING_HANDOFF_GRACE_SECONDS = 90.0
PAIRING_COMPLETION_GRACE_SECONDS = 300.0
PAIRING_ATTEMPT_HARD_TIMEOUT_SECONDS = (
    PAIRING_HANDOFF_GRACE_SECONDS + PAIRING_COMPLETION_GRACE_SECONDS + 15.0
)


class ReverseGattError(RuntimeError):
    """Pairing failure with a stable diagnostic code for Home Assistant."""

    def __init__(self, message: str, detail_code: str) -> None:
        super().__init__(message)
        self.detail_code = detail_code


class SessionMismatchError(ReverseGattError):
    """The nearby app is advertising a different one-time invitation."""


class BondResetRequiredError(ReverseGattError):
    """A verified phone had a stale Windows bond that was reset for retry."""


@dataclass(frozen=True, slots=True)
class ReverseGattResult:
    """Verified peer information returned after the encrypted exchange."""

    address: str
    name: str
    transport: str = "iphone_peripheral"


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
    ) -> None:
        self.service_uuid = service_uuid.lower()
        self.session_uuid = session_uuid.lower()
        self.claim_uuid = claim_uuid.lower()
        self.result_uuid = result_uuid.lower()
        self.progress_callback = progress_callback
        self.detail_code = "waiting_for_iphone_advertisement"
        self._matched_advertisement: Any | None = None
        self._newly_bonded_addresses: set[str] = set()
        self._handoff_deadline: float | None = None
        self._handoff_expires_at: int | None = None
        self._completion_deadline: float | None = None
        self._completion_expires_at: int | None = None

    @property
    def lease_payload(self) -> dict[str, int]:
        """Return non-secret wall-clock deadlines for status reporting."""
        payload: dict[str, int] = {}
        if self._handoff_expires_at is not None:
            payload["attempt_expires_at"] = self._handoff_expires_at
        if self._completion_expires_at is not None:
            payload["completion_expires_at"] = self._completion_expires_at
        return payload

    def _start_handoff_lease(self) -> None:
        if self._handoff_deadline is not None:
            return
        self._handoff_deadline = time.monotonic() + PAIRING_HANDOFF_GRACE_SECONDS
        self._handoff_expires_at = int(time.time() + PAIRING_HANDOFF_GRACE_SECONDS)

    def _start_completion_lease(self) -> None:
        if self._completion_deadline is not None:
            return
        self._completion_deadline = (
            time.monotonic() + PAIRING_COMPLETION_GRACE_SECONDS
        )
        self._completion_expires_at = int(
            time.time() + PAIRING_COMPLETION_GRACE_SECONDS
        )

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
        advertised_name = str(
            getattr(advertisement, "local_name", None)
            or getattr(device, "name", None)
            or ""
        ).strip().casefold()
        # iOS has only 10 bytes of scan-response space for the local name when
        # the 128-bit service UUID consumes the primary advertisement. WinRT
        # can therefore receive "Presence Pair" as the truncated "Presence".
        # The one-time session and HMAC are still verified before accepting it.
        matched = (
            self.service_uuid in services
            or advertised_name in {"presence pair", "presencepair"}
            or advertised_name.startswith("presence")
        )
        if matched:
            self._matched_advertisement = advertisement
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
        winerror = getattr(error, "winerror", None)
        suffix = f" (Windows {winerror})" if winerror is not None else ""
        return f"{type(error).__name__}{suffix}: {detail}".rstrip(": ")[:180]

    @staticmethod
    def _is_resettable_bond_error(error: BaseException) -> bool:
        """Identify WinRT failures that leave or reuse an unusable BLE bond."""
        if isinstance(error, BleakGATTProtocolError) and int(error.code) == 0xF2:
            return True
        return isinstance(error, BleakError) and "could not pair with device" in str(
            error
        ).casefold()

    @classmethod
    def _is_stale_secure_bond_error(cls, error: BaseException) -> bool:
        """Identify a bond that still cannot authorize protected GATT data."""
        if isinstance(error, BleakGATTProtocolError) and int(error.code) == 0x05:
            return True
        return cls._is_resettable_bond_error(error)

    def _log_detected_candidate(self, device: Any) -> None:
        """Record enough radio metadata to diagnose WinRT without QR secrets."""
        advertisement = self._matched_advertisement
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
            kwargs["services"] = [self.service_uuid]
        client = BleakClient(device, **kwargs)
        self._enable_winrt_service_change_retry(client)
        try:
            await asyncio.wait_for(
                client.connect(),
                timeout=connection_timeout,
            )
            return client
        except BaseException:
            with suppress(Exception):
                if client.is_connected:
                    await asyncio.wait_for(client.disconnect(), timeout=5)
            raise

    @staticmethod
    def _enable_winrt_service_change_retry(client: BleakClient) -> None:
        """Refresh discovery when an iPhone publishes its dynamic GATT service."""
        if sys.platform != "win32":
            return
        backend = getattr(client, "_backend", None)
        if backend is None or not hasattr(backend, "_retry_on_services_changed"):
            return
        # CoreBluetooth can emit Services Changed while WinRT is still
        # discovering the freshly published peripheral service. Bleak 3.0
        # contains the correct retry path but leaves it disabled by default.
        backend._retry_on_services_changed = True

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

    async def _open_candidate(
        self,
        device: Any,
        time_budget: float,
        *,
        allow_bond_reset: bool,
    ) -> BleakClient:
        """Open GATT using native, pre-pair, cache, and address fallbacks."""
        deadline = time.monotonic() + min(82.0, time_budget)
        detected_type = self._windows_address_type(device)
        strategies = [
            _ConnectionStrategy(
                "native filtered service discovery",
                detected_type,
                True,
                None,
                False,
                14.0,
            ),
            _ConnectionStrategy(
                "pair before filtered service discovery",
                detected_type,
                True,
                None,
                True,
                28.0,
            ),
            _ConnectionStrategy(
                "cached full service discovery after pairing",
                detected_type,
                False,
                True,
                False,
                14.0,
            ),
            _ConnectionStrategy(
                "fresh filtered service discovery",
                detected_type,
                True,
                False,
                False,
                14.0,
            ),
        ]
        if detected_type is not None:
            strategies.append(
                _ConnectionStrategy(
                    "automatic-address pre-pair fallback",
                    None,
                    True,
                    None,
                    True,
                    20.0,
                )
            )

        last_error: BaseException | None = None
        for index, strategy in enumerate(strategies):
            remaining = deadline - time.monotonic()
            if remaining <= 3:
                break
            attempt_timeout = min(strategy.timeout, max(3.0, remaining - 1.0))
            try:
                return await self._connect_candidate(
                    device,
                    connection_timeout=attempt_timeout,
                    address_type=strategy.address_type,
                    filter_services=strategy.filter_services,
                    use_cached_services=strategy.use_cached_services,
                    pair_before_discovery=strategy.pair_before_discovery,
                )
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
                if allow_bond_reset and self._is_resettable_bond_error(error):
                    self._progress(
                        "iphone_bond_reset",
                        "Windows found an unusable saved phone bond; removing only that bond before retrying",
                    )
                    try:
                        await self._unpair_candidate(
                            device,
                            address_type=detected_type,
                        )
                    except BaseException as unpair_error:
                        raise BondResetRequiredError(
                            "Windows detected a stale phone bond but could not remove it "
                            f"({self._error_summary(unpair_error)})",
                            "iphone_bond_reset_failed",
                        ) from unpair_error
                    raise BondResetRequiredError(
                        "Windows removed the stale phone bond; retrying the active QR session",
                        "iphone_bond_reset",
                    ) from error
                if index + 1 < len(strategies):
                    self._progress(
                        "iphone_connection_retry",
                        "Windows could not finish this Bluetooth route; trying the next compatible route",
                    )
                    await asyncio.sleep(0.35)

        if last_error is None:
            raise TimeoutError("No time remained for a Bluetooth connection")
        if allow_bond_reset and isinstance(last_error, TimeoutError):
            self._progress(
                "iphone_bond_reset",
                "Windows could not reuse the saved phone state; clearing only this Presence Pair bond before retrying",
            )
            try:
                await self._unpair_candidate(device, address_type=detected_type)
            except BaseException as unpair_error:
                LOGGER.warning(
                    "Unable to clear the advertised iPhone bond after connection failure: %s",
                    self._error_summary(unpair_error),
                )
            else:
                raise BondResetRequiredError(
                    "Windows cleared the previous phone bond; retrying the active QR session",
                    "iphone_bond_reset",
                ) from last_error
        raise last_error

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

    async def _reopen_and_read_claim(
        self,
        device: Any,
        link: PairingLink,
        time_budget: float,
    ) -> tuple[BleakClient, bytes | bytearray]:
        """Reconnect after WinRT/iOS closes GATT while committing a new bond."""
        deadline = time.monotonic() + min(60.0, time_budget)
        last_error: BaseException | None = None
        for attempt in range(3):
            remaining = deadline - time.monotonic()
            if remaining <= 3:
                break
            self._progress(
                "iphone_bond_reconnecting",
                "Bluetooth bond accepted; reopening the encrypted app connection",
            )
            bonded_client: BleakClient | None = None
            attempt_error: BaseException | None = None
            try:
                bonded_client = await self._open_candidate(
                    device,
                    min(24.0, remaining),
                    allow_bond_reset=False,
                )
                session_value = await asyncio.wait_for(
                    bonded_client.read_gatt_char(self.session_uuid),
                    timeout=min(10.0, remaining),
                )
                session = self._decode_json(session_value, "session")
                if not self._session_matches(link, session):
                    raise SessionMismatchError(
                        "The iPhone changed pairing session while reconnecting",
                        "iphone_session_mismatch",
                    )
                claim_value = await asyncio.wait_for(
                    bonded_client.read_gatt_char(self.claim_uuid),
                    timeout=min(15.0, remaining),
                )
                return bonded_client, claim_value
            except asyncio.CancelledError:
                raise
            except SessionMismatchError:
                raise
            except BaseException as error:
                last_error = error
                attempt_error = error
                LOGGER.warning(
                    "Encrypted iPhone reconnect %s/3 failed: %s",
                    attempt + 1,
                    self._error_summary(error),
                )
            finally:
                if bonded_client is not None and attempt_error is not None:
                    with suppress(Exception):
                        if bonded_client.is_connected:
                            await asyncio.wait_for(
                                bonded_client.disconnect(),
                                timeout=8,
                            )
            if attempt < 2:
                await asyncio.sleep(0.8 + attempt * 0.6)

        if last_error is None:
            raise TimeoutError("No time remained to reopen the encrypted phone link")
        raise last_error

    async def _read_claim_after_pair(
        self,
        client: BleakClient,
        time_budget: float,
    ) -> bytes | bytearray:
        """Let WinRT finish securing the current GATT link before reconnecting."""
        deadline = time.monotonic() + min(8.0, time_budget)
        last_error: BaseException | None = None
        for attempt in range(4):
            remaining = deadline - time.monotonic()
            if remaining <= 0 or not client.is_connected:
                break
            try:
                return await asyncio.wait_for(
                    client.read_gatt_char(self.claim_uuid),
                    timeout=min(3.0, remaining),
                )
            except asyncio.CancelledError:
                raise
            except BaseException as error:
                last_error = error
                LOGGER.warning(
                    "Encrypted iPhone claim on the existing connection %s/4 "
                    "is not ready: %s",
                    attempt + 1,
                    self._error_summary(error),
                )
                if attempt < 3 and client.is_connected:
                    self._progress(
                        "iphone_bond_settling",
                        "Bluetooth bond accepted; waiting for Windows to secure the current connection",
                    )
                    await asyncio.sleep(0.7 + attempt * 0.4)
        if last_error is None:
            raise ConnectionError("The Bluetooth link closed while securing the bond")
        raise last_error

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
            allow_bond_reset=allow_bond_reset,
        )
        try:
            self._progress(
                "iphone_connected",
                "iPhone connected; checking the active QR session",
            )
            try:
                session_value = await client.read_gatt_char(self.session_uuid)
            except (BleakCharacteristicNotFoundError, BleakGATTProtocolError) as error:
                inventory = self._gatt_inventory(client)
                LOGGER.warning(
                    "Presence Pair GATT session unavailable after connect: %s",
                    inventory,
                )
                schema_blocked = (
                    isinstance(error, BleakCharacteristicNotFoundError)
                    and inventory == "empty"
                )
                if allow_bond_reset and (
                    schema_blocked or self._is_resettable_bond_error(error)
                ):
                    self._progress(
                        "iphone_bond_reset",
                        "Windows retained an incomplete phone bond; removing it before reconnecting",
                    )
                    with suppress(Exception):
                        if client.is_connected:
                            await asyncio.wait_for(client.disconnect(), timeout=8)
                    try:
                        await self._unpair_candidate(
                            device,
                            address_type=self._windows_address_type(device),
                        )
                    except BaseException as unpair_error:
                        raise BondResetRequiredError(
                            "Windows hid the phone service behind an incomplete bond and "
                            f"could not remove it ({self._error_summary(unpair_error)})",
                            "iphone_bond_reset_failed",
                        ) from unpair_error
                    raise BondResetRequiredError(
                        "Windows removed the incomplete phone bond; reconnecting the active QR session",
                        "iphone_bond_reset",
                    ) from error
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
            pair_succeeded = False
            try:
                remaining = max(3.0, deadline - time.monotonic())
                await asyncio.wait_for(client.pair(), timeout=min(30.0, remaining))
                pair_succeeded = True
                self._newly_bonded_addresses.add(
                    str(getattr(device, "address", "") or "").casefold()
                )
                self._progress(
                    "iphone_bond_ready",
                    "Secure Bluetooth bond accepted; finishing the app exchange",
                )
                try:
                    claim_value = await self._read_claim_after_pair(
                        client,
                        max(3.0, deadline - time.monotonic()),
                    )
                except Exception:
                    with suppress(Exception):
                        if client.is_connected:
                            await asyncio.wait_for(client.disconnect(), timeout=8)
                    await asyncio.sleep(0.8)
                    client, claim_value = await self._reopen_and_read_claim(
                        device,
                        link,
                        max(3.0, deadline - time.monotonic()),
                    )
            except asyncio.CancelledError:
                raise
            except Exception as error:
                if (
                    not pair_succeeded
                    and allow_bond_reset
                    and self._is_stale_secure_bond_error(error)
                ):
                    self._progress(
                        "iphone_bond_reset",
                        "The encrypted phone bond remained unusable after reconnecting; Windows is replacing it once",
                    )
                    reset_error: BaseException | None = None
                    try:
                        await asyncio.wait_for(client.unpair(), timeout=15)
                    except BaseException as unpair_error:
                        reset_error = unpair_error
                        LOGGER.warning(
                            "Unable to reset the verified iPhone bond: %s",
                            self._error_summary(unpair_error),
                        )
                    detail = self._error_summary(error)
                    if reset_error is not None:
                        detail += f"; reset: {self._error_summary(reset_error)}"
                    raise BondResetRequiredError(
                        f"A saved phone bond blocked the secure link ({detail}); retrying",
                        "iphone_bond_reset",
                    ) from error
                raise
            claim = self._decode_json(
                claim_value,
                "claim",
            )
            if not self._session_matches(link, claim) or not verify_claim(
                link,
                str(claim.get("proof") or ""),
                allow_expired=True,
            ):
                raise ReverseGattError(
                    "The iPhone did not prove possession of the active QR code",
                    "iphone_claim_rejected",
                )

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
            await client.write_gatt_char(
                self.result_uuid,
                acknowledgement,
                response=True,
            )
            self._progress(
                "iphone_claim_accepted",
                "Encrypted app claim accepted; capturing the private identity",
            )
            return ReverseGattResult(
                address=str(getattr(device, "address", "") or ""),
                name=str(getattr(device, "name", "") or "Presence Pair iPhone"),
            )
        finally:
            with suppress(Exception):
                if client.is_connected:
                    await asyncio.wait_for(client.disconnect(), timeout=8)

    async def async_pair(
        self,
        link: PairingLink,
        timeout_seconds: int,
    ) -> ReverseGattResult:
        """Wait for the matching iPhone, bond, and verify its encrypted claim."""
        link.validate()
        self._handoff_deadline = None
        self._handoff_expires_at = None
        self._completion_deadline = None
        self._completion_expires_at = None
        invitation_deadline = min(
            time.monotonic() + timeout_seconds,
            time.monotonic() + max(1, link.expires_at - int(time.time())),
        )
        last_error: Exception | None = None
        reset_bond_addresses: set[str] = set()
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
            self._start_handoff_lease()
            self._log_detected_candidate(device)
            device_address = str(getattr(device, "address", "") or "").casefold()
            try:
                return await asyncio.wait_for(
                    self._pair_candidate(
                        device,
                        link,
                        max(
                            3.0,
                            (self._completion_deadline or self._handoff_deadline)
                            - time.monotonic(),
                        ),
                        allow_bond_reset=(
                            device_address not in reset_bond_addresses
                            and device_address not in self._newly_bonded_addresses
                        ),
                    ),
                    timeout=PAIRING_ATTEMPT_HARD_TIMEOUT_SECONDS,
                )
            except BondResetRequiredError as err:
                reset_bond_addresses.add(device_address)
                last_error = err
                self._progress(err.detail_code, str(err))
            except SessionMismatchError as err:
                last_error = err
                self._progress(err.detail_code, str(err))
            except asyncio.CancelledError:
                raise
            except Exception as err:
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
            await asyncio.sleep(
                min(1.0, max(0.0, active_deadline - time.monotonic()))
            )

        detail = getattr(last_error, "detail_code", self.detail_code)
        if self._completion_deadline is not None:
            message = (
                "The QR was accepted in time, but secure pairing did not finish "
                "within the five-minute completion window"
            )
        elif self._handoff_deadline is not None:
            message = (
                "The iPhone was found before the code expired, but its QR session "
                "could not be verified within the connection window"
            )
        else:
            message = "The receiver did not find the iPhone before the code expired"
        if last_error is not None:
            message += f". Last step: {last_error}"
        raise ReverseGattError(message, detail) from last_error
