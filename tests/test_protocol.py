"""Protocol compatibility tests."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(
    0,
    str(Path(__file__).parents[1] / "custom_components" / "presence_bridge"),
)

from protocol import (
    PairingLink,
    ProtocolError,
    claim_proof,
    verify_claim,
)

NOW = 1_800_000_000
LINK = PairingLink(
    session_id="abcdefghijklmnopQRSTUVWX",
    observer_id="dell_cucina",
    expires_at=NOW + 180,
    secret=bytes(range(32)),
)


def test_pairing_uri_round_trip() -> None:
    """The app URI is deterministic and round-trips."""
    parsed = PairingLink.from_uri(LINK.to_uri(), now=NOW)
    assert parsed == LINK


def test_claim_vector() -> None:
    """Keep this vector in sync with the Swift unit test."""
    assert claim_proof(LINK) == "ADpo7jfRbxUAzTdGQw-jA3O_tRC_ynKLQ2hHUR94uho"
    assert verify_claim(LINK, claim_proof(LINK), now=NOW)
    assert not verify_claim(LINK, "A" * 43, now=NOW)


def test_expired_invitation_is_rejected() -> None:
    """Expired QR codes cannot reopen pairing sessions."""
    with pytest.raises(ProtocolError, match="expired"):
        PairingLink.from_uri(LINK.to_uri(), now=NOW + 181)


def test_invitation_lifetime_is_limited_to_ten_minutes() -> None:
    """Reject links that bypass the maximum timeout exposed by HA."""
    too_distant = PairingLink(
        session_id=LINK.session_id,
        observer_id=LINK.observer_id,
        expires_at=NOW + 601,
        secret=LINK.secret,
    )
    with pytest.raises(ProtocolError, match="too far"):
        PairingLink.from_uri(too_distant.to_uri(), now=NOW)


@pytest.mark.parametrize(
    "uri",
    [
        "https://example.test/pair",
        "presencepair://pair?v=1",
        "presencepair://pair?v=2&sid=abcdefghijklmnop&oid=bridge_1&exp=1800000180&secret=AA",
    ],
)
def test_malformed_invitation_is_rejected(uri: str) -> None:
    """Malformed or unknown protocol links fail closed."""
    with pytest.raises(ProtocolError):
        PairingLink.from_uri(uri, now=NOW)
