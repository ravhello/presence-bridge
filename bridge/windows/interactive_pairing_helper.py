#!/usr/bin/env python3
"""Run the short-lived iPhone GATT exchange in the logged-in Windows session."""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from gatt_server import GattPairingServer
from protocol import PairingLink
from reverse_gatt_client import ReverseGattPairingClient, ReverseGattResult

LOGGER = logging.getLogger("presence_bridge.interactive_pairing")


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

    def progress(detail_code: str, message: str) -> None:
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
            peer = await client.async_pair(link, timeout_seconds)
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
        )
        raise
    finally:
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
