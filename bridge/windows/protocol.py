"""Shared wire-format helpers for Presence Bridge protocol v1."""

from __future__ import annotations

import base64
import hashlib
import hmac
import re
import time
from dataclasses import dataclass
from urllib.parse import parse_qs, urlencode, urlparse

PAIRING_SCHEME = "presencepair"
PAIRING_HOST = "pair"
PROTOCOL_VERSION = 1
SESSION_ID_RE = re.compile(r"^[A-Za-z0-9_-]{16,96}$")
OBSERVER_ID_RE = re.compile(r"^[a-z0-9_]{3,64}$")


class ProtocolError(ValueError):
    """Raised when a pairing message is invalid."""


def b64url_encode(value: bytes) -> str:
    """Encode bytes without base64 padding."""
    return base64.urlsafe_b64encode(value).decode("ascii").rstrip("=")


def b64url_decode(value: str) -> bytes:
    """Decode unpadded URL-safe base64."""
    if not value or not re.fullmatch(r"[A-Za-z0-9_-]+", value):
        raise ProtocolError("Invalid base64url value")
    try:
        return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))
    except (ValueError, TypeError) as err:
        raise ProtocolError("Invalid base64url value") from err


@dataclass(frozen=True, slots=True)
class PairingLink:
    """One short-lived app-assisted pairing invitation."""

    session_id: str
    observer_id: str
    expires_at: int
    secret: bytes
    version: int = PROTOCOL_VERSION

    def validate(self, *, now: int | None = None, allow_expired: bool = False) -> None:
        """Validate values and the expiry window."""
        if self.version != PROTOCOL_VERSION:
            raise ProtocolError("Unsupported protocol version")
        if not SESSION_ID_RE.fullmatch(self.session_id):
            raise ProtocolError("Invalid session id")
        if not OBSERVER_ID_RE.fullmatch(self.observer_id):
            raise ProtocolError("Invalid observer id")
        if len(self.secret) != 32:
            raise ProtocolError("Pairing secret must contain 32 bytes")
        if not allow_expired:
            current = int(time.time()) if now is None else int(now)
            if self.expires_at <= current:
                raise ProtocolError("Pairing invitation has expired")
            if self.expires_at > current + 900:
                raise ProtocolError(
                    "Pairing invitation expiry is too far in the future"
                )

    def to_uri(self) -> str:
        """Serialize as the URI consumed by the iOS app."""
        self.validate(allow_expired=True)
        query = urlencode(
            {
                "v": self.version,
                "sid": self.session_id,
                "oid": self.observer_id,
                "exp": self.expires_at,
                "secret": b64url_encode(self.secret),
            }
        )
        return f"{PAIRING_SCHEME}://{PAIRING_HOST}?{query}"

    @classmethod
    def from_uri(cls, value: str, *, now: int | None = None) -> PairingLink:
        """Parse and validate a pairing URI."""
        parsed = urlparse(str(value or "").strip())
        if (
            parsed.scheme.lower() != PAIRING_SCHEME
            or parsed.netloc.lower() != PAIRING_HOST
        ):
            raise ProtocolError("Not a Presence Pair invitation")
        values = parse_qs(parsed.query, strict_parsing=True)

        def one(name: str) -> str:
            rows = values.get(name, [])
            if len(rows) != 1:
                raise ProtocolError(f"Missing or repeated field: {name}")
            return rows[0]

        try:
            link = cls(
                version=int(one("v")),
                session_id=one("sid"),
                observer_id=one("oid"),
                expires_at=int(one("exp")),
                secret=b64url_decode(one("secret")),
            )
        except (TypeError, ValueError) as err:
            raise ProtocolError("Invalid numeric field") from err
        link.validate(now=now)
        return link


def claim_message(
    session_id: str,
    observer_id: str,
    expires_at: int,
) -> bytes:
    """Return the canonical byte sequence authenticated by the app."""
    return (
        f"presence-bridge:v{PROTOCOL_VERSION}\n"
        f"{session_id}\n{observer_id}\n{int(expires_at)}"
    ).encode("ascii")


def claim_proof(link: PairingLink) -> str:
    """Build the app's HMAC proof without transmitting the QR secret."""
    link.validate(allow_expired=True)
    digest = hmac.new(
        link.secret,
        claim_message(link.session_id, link.observer_id, link.expires_at),
        hashlib.sha256,
    ).digest()
    return b64url_encode(digest)


def verify_claim(
    link: PairingLink,
    supplied_proof: str,
    *,
    now: int | None = None,
) -> bool:
    """Verify an app claim in constant time."""
    link.validate(now=now)
    return hmac.compare_digest(claim_proof(link), str(supplied_proof or ""))


def public_session_payload(link: PairingLink) -> dict[str, int | str]:
    """Return the non-secret GATT session descriptor."""
    return {
        "v": link.version,
        "sid": link.session_id,
        "oid": link.observer_id,
        "exp": link.expires_at,
    }
