"""Scoped read-only signal credentials, independent of Bluetooth pairing."""

import hashlib
import hmac
import re
import secrets
import time
from urllib.parse import urlsplit

TOKEN_RE = re.compile(r"^[A-Za-z0-9_-]{43}$")


def new_access(lifetime: int) -> tuple[str, dict]:
    token = secrets.token_urlsafe(32)
    return token, {
        "digest": hashlib.sha256(token.encode()).hexdigest(),
        "expires": int(time.time()) + lifetime,
    }


def matches_access(token: str, record: dict | None) -> bool:
    if not TOKEN_RE.fullmatch(token) or not isinstance(record, dict):
        return False
    return (
        isinstance(record.get("expires"), int)
        and record["expires"] > time.time()
        and hmac.compare_digest(
            hashlib.sha256(token.encode()).hexdigest(), str(record.get("digest", ""))
        )
    )


def monitor_origin(value: str) -> str:
    url = urlsplit(value)
    if (
        url.scheme != "https"
        or not url.hostname
        or url.username
        or url.password
        or url.query
        or url.fragment
        or url.path not in ("", "/")
        or url.hostname in ("localhost", "127.0.0.1", "::1")
    ):
        raise ValueError(
            "Use the HTTPS address of Home Assistant reachable from the iPhone"
        )
    return value.rstrip("/")
