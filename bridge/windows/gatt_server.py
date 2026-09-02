"""Temporary Windows GATT server used by the Presence Pair iOS app."""

from __future__ import annotations

import asyncio
import json
import logging
import sys
import uuid
from typing import Any

from protocol import PairingLink, public_session_payload, verify_claim

LOGGER = logging.getLogger("presence_bridge.gatt")
ATT_ERROR_UNLIKELY = 0x0E
ATT_ERROR_AUTHORIZATION = 0x08


def _buffer(value: bytes) -> Any:
    from winrt.windows.storage.streams import DataWriter

    writer = DataWriter()
    writer.write_bytes(value)
    return writer.detach_buffer()


class GattPairingServer:
    """Advertise one time-bound encrypted-write GATT service."""

    def __init__(
        self,
        *,
        service_uuid: str,
        session_uuid: str,
        claim_uuid: str,
        result_uuid: str,
    ) -> None:
        self.service_uuid = service_uuid
        self.session_uuid = session_uuid
        self.claim_uuid = claim_uuid
        self.result_uuid = result_uuid
        self._provider: Any = None
        self._claim_characteristic: Any = None
        self._claim_token: Any = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._link: PairingLink | None = None
        self._claim_future: asyncio.Future[dict[str, Any]] | None = None

    async def async_start(self, link: PairingLink) -> None:
        """Create characteristics and begin connectable advertising."""
        if sys.platform != "win32":
            raise RuntimeError("The app-assisted GATT server requires Windows")
        link.validate()
        self._loop = asyncio.get_running_loop()
        self._link = link
        self._claim_future = self._loop.create_future()

        from winrt.windows.devices.bluetooth.genericattributeprofile import (
            GattCharacteristicProperties,
            GattLocalCharacteristicParameters,
            GattProtectionLevel,
            GattServiceProvider,
            GattServiceProviderAdvertisingParameters,
        )

        try:
            provider_result = await GattServiceProvider.create_async(
                uuid.UUID(self.service_uuid)
            )
            if (
                int(provider_result.error) != 0
                or provider_result.service_provider is None
            ):
                raise RuntimeError(
                    f"Unable to create GATT provider: {provider_result.error}"
                )
            self._provider = provider_result.service_provider

            session_parameters = GattLocalCharacteristicParameters()
            session_parameters.characteristic_properties = (
                GattCharacteristicProperties.READ
            )
            session_parameters.read_protection_level = GattProtectionLevel.PLAIN
            session_parameters.user_description = "Presence Bridge pairing session"
            session_parameters.static_value = _buffer(
                json.dumps(
                    public_session_payload(link),
                    separators=(",", ":"),
                ).encode("utf-8")
            )
            session_result = await self._provider.service.create_characteristic_async(
                uuid.UUID(self.session_uuid),
                session_parameters,
            )
            if int(session_result.error) != 0:
                raise RuntimeError(
                    f"Unable to create session characteristic: {session_result.error}"
                )

            claim_parameters = GattLocalCharacteristicParameters()
            claim_parameters.characteristic_properties = (
                GattCharacteristicProperties.WRITE
            )
            claim_parameters.write_protection_level = (
                GattProtectionLevel.ENCRYPTION_REQUIRED
            )
            claim_parameters.user_description = "Encrypted Presence Pair claim"
            claim_result = await self._provider.service.create_characteristic_async(
                uuid.UUID(self.claim_uuid),
                claim_parameters,
            )
            if int(claim_result.error) != 0 or claim_result.characteristic is None:
                raise RuntimeError(
                    f"Unable to create claim characteristic: {claim_result.error}"
                )
            self._claim_characteristic = claim_result.characteristic
            self._claim_token = self._claim_characteristic.add_write_requested(
                self._on_write_requested
            )

            result_parameters = GattLocalCharacteristicParameters()
            result_parameters.characteristic_properties = (
                GattCharacteristicProperties.READ
            )
            result_parameters.read_protection_level = (
                GattProtectionLevel.ENCRYPTION_REQUIRED
            )
            result_parameters.user_description = "Presence Pair result"
            result_parameters.static_value = _buffer(b'{"status":"ready"}')
            result_result = await self._provider.service.create_characteristic_async(
                uuid.UUID(self.result_uuid),
                result_parameters,
            )
            if int(result_result.error) != 0:
                raise RuntimeError(
                    f"Unable to create result characteristic: {result_result.error}"
                )

            advertising = GattServiceProviderAdvertisingParameters()
            advertising.is_connectable = True
            advertising.is_discoverable = True
            self._provider.start_advertising_with_parameters(advertising)
            LOGGER.info("Temporary Presence Pair GATT service is advertising")
        except Exception:
            await self.async_stop()
            raise

    async def async_wait_for_claim(self, timeout_seconds: float) -> dict[str, Any]:
        """Wait until the app proves possession of the QR secret."""
        if self._claim_future is None:
            raise RuntimeError("GATT server has not started")
        return await asyncio.wait_for(
            asyncio.shield(self._claim_future),
            timeout=timeout_seconds,
        )

    def _on_write_requested(self, sender: Any, args: Any) -> None:
        if self._loop is None or self._loop.is_closed():
            return
        asyncio.run_coroutine_threadsafe(self._async_handle_write(args), self._loop)

    async def _async_handle_write(self, args: Any) -> None:
        deferral = args.get_deferral()
        request = None
        try:
            request = await args.get_request_async()
            if request is None:
                return
            raw = bytes(request.value)
            if len(raw) > 1024:
                request.respond_with_protocol_error(ATT_ERROR_AUTHORIZATION)
                return
            try:
                payload = json.loads(raw.decode("utf-8"))
                link = self._link
                valid = bool(
                    isinstance(payload, dict)
                    and link is not None
                    and payload.get("v") == link.version
                    and payload.get("sid") == link.session_id
                    and payload.get("oid") == link.observer_id
                    and payload.get("exp") == link.expires_at
                    and verify_claim(link, str(payload.get("proof") or ""))
                )
            except (UnicodeDecodeError, ValueError, TypeError):
                valid = False
                payload = {}
            if not valid:
                request.respond_with_protocol_error(ATT_ERROR_AUTHORIZATION)
                LOGGER.warning("Rejected invalid app pairing claim")
                return
            request.respond()
            if self._claim_future is not None and not self._claim_future.done():
                self._claim_future.set_result(
                    {
                        "session_id": link.session_id,
                        "claim_verified": True,
                    }
                )
        except Exception:
            LOGGER.exception("Unable to process the app pairing claim")
            try:
                if request is not None:
                    request.respond_with_protocol_error(ATT_ERROR_UNLIKELY)
            except Exception:
                pass
        finally:
            deferral.complete()

    async def async_stop(self) -> None:
        """Stop advertising and release all WinRT objects."""
        if self._claim_characteristic is not None and self._claim_token is not None:
            try:
                self._claim_characteristic.remove_write_requested(self._claim_token)
            except Exception:
                LOGGER.debug("Write handler was already removed", exc_info=True)
        if self._provider is not None:
            try:
                self._provider.stop_advertising()
            except Exception:
                LOGGER.debug("GATT advertising was already stopped", exc_info=True)
        self._claim_characteristic = None
        self._claim_token = None
        self._provider = None
        self._link = None
        if self._claim_future is not None and not self._claim_future.done():
            self._claim_future.cancel()
        self._claim_future = None
