"""Connect to the short-lived GATT service advertised by Presence Pair."""

from __future__ import annotations

import asyncio
import json
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from bleak import BleakClient, BleakScanner
from protocol import PairingLink, acceptance_proof, verify_claim

ProgressCallback = Callable[[str, str], None]


class ReverseGattError(RuntimeError):
    """Pairing failure with a stable diagnostic code for Home Assistant."""

    def __init__(self, message: str, detail_code: str) -> None:
        super().__init__(message)
        self.detail_code = detail_code


class SessionMismatchError(ReverseGattError):
    """The nearby app is advertising a different one-time invitation."""


@dataclass(frozen=True, slots=True)
class ReverseGattResult:
    """Verified peer information returned after the encrypted exchange."""

    address: str
    name: str


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

    def _progress(self, detail_code: str, message: str) -> None:
        self.detail_code = detail_code
        if self.progress_callback is not None:
            self.progress_callback(detail_code, message)

    def _matches_advertisement(self, _device: Any, advertisement: Any) -> bool:
        services = {
            str(value).lower()
            for value in (getattr(advertisement, "service_uuids", None) or [])
        }
        return self.service_uuid in services

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

    async def _pair_candidate(
        self,
        device: Any,
        link: PairingLink,
        time_budget: float,
    ) -> ReverseGattResult:
        self._progress(
            "iphone_advertisement_seen",
            "iPhone found; opening the local Bluetooth connection",
        )
        client = BleakClient(
            device,
            timeout=min(20.0, time_budget),
            services=[self.service_uuid],
        )
        try:
            await asyncio.wait_for(
                client.connect(), timeout=min(22.0, time_budget)
            )
            self._progress(
                "iphone_connected",
                "iPhone connected; checking the active QR session",
            )
            session = self._decode_json(
                await client.read_gatt_char(self.session_uuid),
                "session",
            )
            if not self._session_matches(link, session):
                raise SessionMismatchError(
                    "A nearby iPhone is using a different or expired QR code",
                    "iphone_session_mismatch",
                )

            self._progress(
                "iphone_session_verified",
                "QR session verified; securing the Bluetooth link",
            )
            await client.pair()
            self._progress(
                "iphone_bond_ready",
                "Secure Bluetooth bond ready; reading the encrypted app claim",
            )
            claim = self._decode_json(
                await client.read_gatt_char(self.claim_uuid),
                "claim",
            )
            if not self._session_matches(link, claim) or not verify_claim(
                link,
                str(claim.get("proof") or ""),
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
            if client.is_connected:
                await client.disconnect()

    async def async_pair(
        self,
        link: PairingLink,
        timeout_seconds: int,
    ) -> ReverseGattResult:
        """Wait for the matching iPhone, bond, and verify its encrypted claim."""
        link.validate()
        deadline = min(
            time.monotonic() + timeout_seconds,
            time.monotonic() + max(1, link.expires_at - int(time.time())),
        )
        last_error: Exception | None = None
        self._progress(
            "waiting_for_iphone_advertisement",
            "Receiver ready; scan the QR code and keep Presence Pair open",
        )
        while (remaining := deadline - time.monotonic()) > 0:
            device = await BleakScanner.find_device_by_filter(
                self._matches_advertisement,
                timeout=min(8.0, remaining),
            )
            if device is None:
                continue
            try:
                return await asyncio.wait_for(
                    self._pair_candidate(device, link, remaining),
                    timeout=min(55.0, remaining),
                )
            except SessionMismatchError as err:
                last_error = err
                self._progress(err.detail_code, str(err))
            except asyncio.CancelledError:
                raise
            except Exception as err:
                last_error = err
                self._progress(
                    getattr(err, "detail_code", "iphone_connection_failed"),
                    f"iPhone connection failed ({type(err).__name__}); retrying",
                )
            await asyncio.sleep(min(1.0, max(0.0, deadline - time.monotonic())))

        detail = getattr(last_error, "detail_code", self.detail_code)
        message = "The receiver did not find the iPhone before the code expired"
        if last_error is not None:
            message += f". Last step: {last_error}"
        raise ReverseGattError(message, detail) from last_error
