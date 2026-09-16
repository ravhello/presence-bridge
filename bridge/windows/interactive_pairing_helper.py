#!/usr/bin/env python3
"""Run the short-lived iPhone GATT exchange in the logged-in Windows session."""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import re
import time
from contextlib import suppress
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from gatt_server import GattAdvertisingError, GattPairingServer, GattProximityServer
from identity_removal import BondDevice, compact_address, unpair_device
from numeric_pairing_probe import QRSessionPairing
from protocol import PairingLink
from reverse_gatt_client import (
    GattMetadataLogFilter,
    ReverseGattPairingClient,
    ReverseGattResult,
)

LOGGER = logging.getLogger("presence_bridge.interactive_pairing")
PREFLIGHT_READY_UUID = "b6201f73-89f1-4c2b-981f-7ccade5a52d4"
PROXIMITY_PROVIDER_START_ATTEMPTS = 3
PROXIMITY_PROVIDER_RETRY_SECONDS = 2.0


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temporary.write_text(
        json.dumps(payload, separators=(",", ":"), sort_keys=True),
        encoding="utf-8",
    )
    try:
        for attempt in range(8):
            try:
                temporary.replace(path)
                return
            except PermissionError:
                if attempt == 7:
                    raise
                # Windows can briefly lock the old result while the observer
                # reads it. Preserve atomic replacement and retry the rename.
                time.sleep(0.025 * (attempt + 1))
    finally:
        temporary.unlink(missing_ok=True)


def _status(
    path: Path,
    session_id: str,
    state: str,
    *,
    detail_code: str,
    message: str,
    **extra: Any,
) -> None:
    payload = {
        "schema": 1,
        "session_id": session_id,
        "state": state,
        "detail_code": detail_code,
        "message": message,
        "updated_at": datetime.now(UTC).isoformat(),
        **extra,
    }
    _write_json(path, payload)
    LOGGER.info("%s: %s", detail_code, message)


async def _start_proximity_server(
    link: PairingLink,
    result_path: Path,
) -> GattProximityServer:
    """Start a fresh WinRT provider, tolerating delayed adapter release."""
    last_error: Exception | None = None
    for attempt in range(1, PROXIMITY_PROVIDER_START_ATTEMPTS + 1):
        server = GattProximityServer(ready_uuid=PREFLIGHT_READY_UUID)
        try:
            await server.async_start(link)
            return server
        except asyncio.CancelledError:
            await server.async_stop()
            raise
        except Exception as error:
            last_error = error
            await server.async_stop()
            if attempt >= PROXIMITY_PROVIDER_START_ATTEMPTS:
                if isinstance(error, GattAdvertisingError):
                    raise
                raise GattAdvertisingError(
                    "The Windows receiver could not start its Bluetooth signal. "
                    f"Check the receiver adapter: {type(error).__name__}: {error}"
                ) from error
            _status(
                result_path,
                link.session_id,
                "progress",
                detail_code="windows_adapter_recovering",
                message=(
                    "The receiver could not start Bluetooth advertising; "
                    "retrying the local service without changing phone pairings"
                ),
                retry_attempt=attempt,
            )
            LOGGER.warning(
                "Proximity provider start %s/%s failed; creating a fresh provider",
                attempt,
                PROXIMITY_PROVIDER_START_ATTEMPTS,
            )
            await asyncio.sleep(PROXIMITY_PROVIDER_RETRY_SECONDS)
    assert last_error is not None
    raise last_error


async def _run(command_path: Path, result_path: Path) -> int:
    command_text = command_path.read_text(encoding="utf-8-sig")
    command_path.unlink(missing_ok=True)
    raw = json.loads(command_text)
    if raw.get("transport") == "identity_removal":
        return await _remove_bonds(raw, result_path)
    failure_receipt = raw.get("transport") == "failure_beacon"
    receipt = failure_receipt or raw.get("transport") == "completion_beacon"
    link = PairingLink.from_uri(str(raw["pairing_uri"]), allow_expired=receipt)
    if str(raw.get("session_id") or "") != link.session_id:
        raise ValueError("Interactive pairing command session mismatch")
    if receipt:
        # Only SYSTEM can request a terminal receipt. Failure never means commit.
        remaining = float(raw["attempt_expires_at"]) - time.time()
        if not 0 < remaining <= 300:
            raise ValueError("Completion receipt is outside the active attempt")
        server = GattProximityServer(
            ready_uuid=PREFLIGHT_READY_UUID,
            **(
                {"failure_receipt": True}
                if failure_receipt
                else {"completion_receipt": True}
            ),
        )
        try:
            async with asyncio.timeout(remaining):
                await server.async_start(link)
                _status(
                    result_path,
                    link.session_id,
                    "progress",
                    detail_code="failure_beacon_advertising"
                    if failure_receipt
                    else "completion_beacon_advertising",
                    message="The receiver stopped this attempt without completing enrollment"
                    if failure_receipt
                    else "Home Assistant verified and saved this iPhone",
                )
                await asyncio.sleep(
                    min(20, max(0, float(raw["attempt_expires_at"]) - time.time()))
                )
        finally:
            await server.async_stop()
        _status(
            result_path,
            link.session_id,
            "success",
            detail_code="failure_beacon_sent"
            if failure_receipt
            else "completion_beacon_sent",
            message="Terminal failure receipt transmitted"
            if failure_receipt
            else "Home Assistant completion receipt transmitted",
        )
        return 0
    gatt = raw["gatt"]
    transport = str(raw.get("transport") or "iphone_peripheral")
    timeout_seconds = max(60, min(600, int(raw.get("timeout_seconds", 180))))
    client: ReverseGattPairingClient | None = None
    client_task: asyncio.Task[ReverseGattResult] | None = None
    preflight_task: asyncio.Task[dict[str, Any]] | None = None
    iphone_seen_task: asyncio.Task[bool] | None = None
    iphone_seen = asyncio.Event()
    proximity_waiting = False

    def progress(detail_code: str, message: str) -> None:
        if detail_code == "iphone_advertisement_seen":
            iphone_seen.set()
        if proximity_waiting and detail_code in {
            "waiting_for_iphone_advertisement",
            "windows_adapter_recovering",
        }:
            return
        _status(
            result_path,
            link.session_id,
            "progress",
            detail_code=detail_code,
            message=message,
            **(client.lease_payload if client is not None else {}),
        )

    _status(
        result_path,
        link.session_id,
        "progress",
        detail_code="interactive_receiver_ready",
        message="The logged-in Windows Bluetooth session is ready for the iPhone",
    )
    server: GattPairingServer | None = None
    proximity_server: GattProximityServer | None = None
    try:
        if transport == "windows_peripheral":
            server = GattPairingServer(
                service_uuid=str(gatt["service_uuid"]),
                session_uuid=str(gatt["session_uuid"]),
                claim_uuid=str(gatt["claim_uuid"]),
                result_uuid=str(gatt["result_uuid"]),
            )
            await server.async_start(link)
            _status(
                result_path,
                link.session_id,
                "progress",
                detail_code="legacy_receiver_advertising",
                message="Receiver is visible to this Presence Pair build",
            )
            await server.async_wait_for_claim(timeout_seconds)
            peer = ReverseGattResult(
                address="",
                name="Presence Pair iPhone",
                transport=transport,
                secure_exchange_complete=True,
            )
        elif transport == "iphone_peripheral":
            client = ReverseGattPairingClient(
                service_uuid=str(gatt["service_uuid"]),
                session_uuid=str(gatt["session_uuid"]),
                claim_uuid=str(gatt["claim_uuid"]),
                result_uuid=str(gatt["result_uuid"]),
                progress_callback=progress,
                pairing_probe=QRSessionPairing(
                    result_path.parent, _write_json, progress
                ),
            )
            proximity_server = await _start_proximity_server(link, result_path)
            proximity_waiting = True
            _status(
                result_path,
                link.session_id,
                "progress",
                detail_code="receiver_proximity_check",
                message=(
                    "Receiver beacon ready; the iPhone will start pairing "
                    "automatically when the signal is strong enough"
                ),
            )
            # Keep the previous direct path alive for already released app builds.
            # The QR-scoped beacon is a different service and cannot match this scan.
            client_task = asyncio.create_task(client.async_pair(link, timeout_seconds))
            preflight_task = asyncio.create_task(
                proximity_server.async_wait_until_ready(timeout_seconds)
            )
            iphone_seen_task = asyncio.create_task(iphone_seen.wait())
            done, _pending = await asyncio.wait(
                {client_task, preflight_task, iphone_seen_task},
                return_when=asyncio.FIRST_COMPLETED,
            )
            if client_task in done:
                peer = await client_task
            else:
                if preflight_task in done:
                    await preflight_task
                else:
                    await iphone_seen_task
                # The phone has either read the QR-derived proximity service or
                # started advertising the exact session service. From this point
                # QR expiry must not interrupt the WinRT role switch.
                client.start_handoff_lease()
                proximity_waiting = False
                _status(
                    result_path,
                    link.session_id,
                    "progress",
                    detail_code="receiver_proximity_confirmed",
                    message=(
                        "iPhone is close enough; secure pairing is starting "
                        "automatically"
                    ),
                    **client.lease_payload,
                )
                await proximity_server.async_stop()
                proximity_server = None
                peer = await client_task
        else:
            raise ValueError(f"Unsupported pairing transport: {transport}")
    except BaseException as error:
        detail_code = str(
            getattr(error, "detail_code", None)
            or getattr(client, "detail_code", None)
            or "interactive_pairing_failed"
        )
        _status(
            result_path,
            link.session_id,
            "error",
            detail_code=detail_code,
            failure_state=getattr(error, "terminal_state", "error"),
            message=f"{type(error).__name__}: {str(error).strip()}"[:300],
            **(client.lease_payload if client is not None else {}),
        )
        raise
    finally:
        for task in (iphone_seen_task, preflight_task, client_task):
            if task is not None:
                if not task.done():
                    task.cancel()
                with suppress(BaseException):
                    await task
        if proximity_server is not None:
            await proximity_server.async_stop()
        if server is not None:
            await server.async_stop()

    _status(
        result_path,
        link.session_id,
        "success",
        detail_code="iphone_claim_accepted",
        message="The iPhone completed the encrypted exchange",
        address=peer.address,
        name=peer.name,
        transport=peer.transport,
        secure_exchange_complete=peer.secure_exchange_complete,
    )
    return 0


async def _remove_bonds(raw: dict[str, Any], result_path: Path) -> int:
    request_id = str(raw.get("session_id") or "")
    if not re.fullmatch(r"[A-Za-z0-9_-]{16,96}", request_id):
        raise ValueError("Invalid Windows removal request")
    remaining = float(raw.get("attempt_expires_at") or 0) - time.time()
    if not 0 < remaining <= 55:
        raise ValueError("Expired Windows removal request")
    targets = [BondDevice(**row) for row in raw.get("targets", [])]
    if not 1 <= len(targets) <= 8 or any(
        not compact_address(target.address)
        or target.transport not in {"classic", "ble"}
        or not target.device_id.startswith(("Bluetooth#", "BluetoothLE#"))
        for target in targets
    ):
        raise ValueError("Invalid Windows removal targets")
    try:
        async with asyncio.timeout(remaining):
            for target in targets:
                await unpair_device(target)
        _status(
            result_path,
            request_id,
            "success",
            detail_code="windows_bonds_removed",
            message="Windows removal completed; the service must verify key absence",
            removed_target_ids=[target.device_id for target in targets],
        )
        return 0
    except Exception as error:
        _status(
            result_path,
            request_id,
            "error",
            detail_code="windows_removal_failed",
            message=str(error) or type(error).__name__,
        )
        return 1


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--command", required=True, type=Path)
    parser.add_argument("--result", required=True, type=Path)
    parser.add_argument("--log", required=True, type=Path)
    args = parser.parse_args()
    args.log.parent.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
        handlers=[logging.FileHandler(args.log, encoding="utf-8")],
    )
    logging.getLogger("bleak.backends.winrt.client").setLevel(logging.DEBUG)
    logging.getLogger("bleak.backends.winrt.client").addFilter(GattMetadataLogFilter())

    if not args.command.is_file():
        LOGGER.error("Interactive pairing command is missing")
        return 2
    try:
        return asyncio.run(_run(args.command, args.result))
    except BaseException:
        LOGGER.exception("Interactive iPhone pairing failed")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
