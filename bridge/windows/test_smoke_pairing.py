"""Tests for the observer-level pairing smoke test."""

from __future__ import annotations

import base64

from cryptography.hazmat.primitives import serialization
from protocol import b64url_decode
from smoke_pairing import build_start_payload


def test_smoke_payload_is_valid_and_contains_no_private_key() -> None:
    payload = build_start_payload("observer_1", now=1_800_000_000)
    assert payload["action"] == "start_app_pairing"
    assert payload["observer_id"] == "observer_1"
    assert payload["expires_at"] == 1_800_000_090
    assert len(b64url_decode(payload["app_secret"])) == 32
    serialization.load_der_public_key(base64.b64decode(payload["public_key"]))
    assert "private_key" not in payload
    assert set(payload["gatt"]) == {
        "service_uuid",
        "session_uuid",
        "claim_uuid",
        "result_uuid",
    }
