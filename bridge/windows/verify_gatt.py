#!/usr/bin/env python3
"""Verify that the local Windows adapter can host the pairing service."""

from __future__ import annotations

import argparse
import asyncio
import json
import secrets
import time

from gatt_server import GattPairingServer
from protocol import PairingLink

TEST_UUIDS = {
    "service_uuid": "96c866ef-137b-4e5a-9b56-c22cd7a59f2b",
    "session_uuid": "1cd724e6-2bf7-4c45-bc81-615c94f780cf",
    "claim_uuid": "79f167a4-81e1-4de1-86b4-a566323f5938",
    "result_uuid": "34062c12-1888-42cb-a3d3-235a68818acb",
}


async def verify(advertise_seconds: float) -> None:
    """Create a unique service, advertise briefly, and always tear it down."""
    link = PairingLink(
        session_id=secrets.token_urlsafe(24),
        observer_id="adapter_test",
        expires_at=int(time.time()) + 120,
        secret=secrets.token_bytes(32),
    )
    server = GattPairingServer(**TEST_UUIDS)
    started = time.monotonic()
    advertisement_status = "not_started"
    advertisement_error = "none"
    try:
        await server.async_start(link)
        advertisement_status = server.advertisement_status
        advertisement_error = server.advertisement_error
        await asyncio.sleep(max(1.0, min(60.0, advertise_seconds)))
    except Exception as error:
        advertisement_status = server.advertisement_status
        advertisement_error = server.advertisement_error
        print(
            json.dumps(
                {
                    "compatible": False,
                    "advertisement_status": advertisement_status,
                    "advertisement_error": advertisement_error,
                    "error": str(error),
                }
            )
        )
        raise
    finally:
        await server.async_stop()
    print(
        json.dumps(
            {
                "compatible": True,
                "advertisement_status": advertisement_status,
                "advertisement_error": advertisement_error,
                "advertised_seconds": round(time.monotonic() - started, 2),
                "stopped_cleanly": True,
            }
        )
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--seconds", type=float, default=2.0)
    args = parser.parse_args()
    asyncio.run(verify(args.seconds))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
