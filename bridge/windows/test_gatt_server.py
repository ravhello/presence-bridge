"""Platform-independent tests for the legacy Windows GATT server."""

from __future__ import annotations

import asyncio

import gatt_server
import pytest
from gatt_server import advertisement_status_name, winrt_error_name


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
