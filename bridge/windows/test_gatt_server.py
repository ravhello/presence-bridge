"""Platform-independent validation around the GATT claim payload."""

from __future__ import annotations

import asyncio
import json

import gatt_server
import pytest
from gatt_server import advertisement_status_name, winrt_error_name
from protocol import PairingLink, ProtocolError, claim_proof, verify_claim


@pytest.mark.parametrize(
    ("status", "expected"),
    [
        (0, "created"),
        (1, "stopped"),
        (2, "started"),
        (3, "aborted"),
        (4, "started_without_all_data"),
        (99, "unknown_99"),
        (None, "unknown"),
    ],
)
def test_advertisement_status_name(status: object, expected: str) -> None:
    assert advertisement_status_name(status) == expected


def test_winrt_error_name_uses_numeric_fallback() -> None:
    assert winrt_error_name(3) == "code_3"
    assert winrt_error_name(None) == "unknown"


def test_advertising_wait_ignores_transient_aborted_status(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class Provider:
        statuses = iter((3, 2))
        current = 3

        @property
        def advertisement_status(self) -> int:
            self.current = next(self.statuses, self.current)
            return self.current

    monkeypatch.setattr(gatt_server, "ADVERTISEMENT_POLL_SECONDS", 0.001)
    server = gatt_server.GattPairingServer(
        service_uuid="service",
        session_uuid="session",
        claim_uuid="claim",
        result_uuid="result",
    )
    server._provider = Provider()

    asyncio.run(server._async_wait_until_advertising())

    assert server.advertisement_status == "started"


def test_claim_payload_round_trip() -> None:
    link = PairingLink(
        session_id="abcdefghijklmnopQRSTUVWX",
        observer_id="observer_1",
        expires_at=1_800_000_180,
        secret=bytes(range(32)),
    )
    encoded = json.dumps(
        {
            "v": 1,
            "sid": link.session_id,
            "oid": link.observer_id,
            "exp": link.expires_at,
            "proof": claim_proof(link),
        },
        separators=(",", ":"),
    ).encode()
    decoded = json.loads(encoded)
    assert verify_claim(link, decoded["proof"], now=1_800_000_000)


def test_invitation_lifetime_is_limited_to_ten_minutes() -> None:
    link = PairingLink(
        session_id="abcdefghijklmnopQRSTUVWX",
        observer_id="observer_1",
        expires_at=1_800_000_601,
        secret=bytes(range(32)),
    )
    with pytest.raises(ProtocolError, match="too far"):
        PairingLink.from_uri(link.to_uri(), now=1_800_000_000)
