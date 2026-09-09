#!/usr/bin/env python3
"""Run the short-lived iPhone GATT exchange in the logged-in Windows session."""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import time
from contextlib import suppress
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from gatt_server import GattPairingServer, GattProximityServer
from protocol import PairingLink
from reverse_gatt_client import ReverseGattPairingClient, ReverseGattResult

LOGGER = logging.getLogger("presence_bridge.interactive_pairing")
PREFLIGHT_READY_UUID = "b6201f73-89f1-4c2b-981f-7ccade5a52d4"
PROXIMITY_PROVIDER_START_ATTEMPTS = 5
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
                raise
            _status(
                result_path,
                link.session_id,
                "progress",
                detail_code="windows_adapter_recovering",
                message=(
                    "Windows is releasing the previous Bluetooth session; "
                    "retrying automatically"
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
    link = PairingLink.from_uri(str(raw["pairing_uri"]))
    if str(raw.get("session_id") or "") != link.session_id:
        raise ValueError("Interactive pairing command session mismatch")
    gatt = raw["gatt"]
    transport = str(raw.get("transport") or "iphone_peripheral")
    timeout_seconds = max(60, min(600, int(raw.get("timeout_seconds", 180))))
    client: ReverseGattPairingClient | None = None
    client_task: asyncio.Task[ReverseGattResult] | None = None
    preflight_task: asyncio.Task[dict[str, Any]] | None = None
    proximity_waiting = False

    def progress(detail_code: str, message: str) -> None:
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
            )
        elif transport == "iphone_peripheral":
            client = ReverseGattPairingClient(
                service_uuid=str(gatt["service_uuid"]),
                session_uuid=str(gatt["session_uuid"]),
                claim_uuid=str(gatt["claim_uuid"]),
                result_uuid=str(gatt["result_uuid"]),
                progress_callback=progress,
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
            client_task = asyncio.create_task(
                client.async_pair(link, timeout_seconds)
            )
            preflight_task = asyncio.create_task(
                proximity_server.async_wait_until_ready(timeout_seconds)
            )
            done, _pending = await asyncio.wait(
                {client_task, preflight_task},
                return_when=asyncio.FIRST_COMPLETED,
            )
            if client_task in done:
                peer = await client_task
            else:
                await preflight_task
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
            message=f"{type(error).__name__}: {str(error).strip()}"[:300],
            **(client.lease_payload if client is not None else {}),
        )
        raise
    finally:
        for task in (preflight_task, client_task):
            if task is not None and not task.done():
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
    )
    return 0


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
