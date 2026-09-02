"""Platform-independent validation around the GATT claim payload."""

from __future__ import annotations

import json

from protocol import PairingLink, claim_proof, verify_claim


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
