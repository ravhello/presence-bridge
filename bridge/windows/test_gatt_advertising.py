"""Bounded receiver advertising diagnostics without a Windows radio."""

import asyncio
from types import SimpleNamespace

import gatt_server as gatt
import pytest


def server(provider):
    result = gatt.GattPairingServer(
        service_uuid="service",
        session_uuid="session",
        claim_uuid="claim",
        result_uuid="result",
    )
    result._provider = provider
    return result


@pytest.mark.parametrize(
    "status", [gatt.ADVERTISEMENT_STARTED, gatt.ADVERTISEMENT_STARTED_WITHOUT_ALL_DATA]
)
def test_started_advertising_is_accepted(status):
    instance = server(SimpleNamespace(advertisement_status=status))
    asyncio.run(instance._async_wait_until_advertising())


def test_brief_startup_abort_can_recover(monkeypatch):
    class Provider:
        reads = 0

        @property
        def advertisement_status(self):
            self.reads += 1
            return (
                gatt.ADVERTISEMENT_ABORTED
                if self.reads < 3
                else gatt.ADVERTISEMENT_STARTED
            )

    monkeypatch.setattr(gatt, "ADVERTISEMENT_POLL_SECONDS", 0.001)
    asyncio.run(server(Provider())._async_wait_until_advertising())


def test_persistent_abort_is_receiver_error_not_phone_wait(monkeypatch):
    monkeypatch.setattr(gatt, "ADVERTISEMENT_POLL_SECONDS", 0.001)
    monkeypatch.setattr(gatt, "ADVERTISEMENT_ABORTED_GRACE_SECONDS", 0.01)
    instance = server(SimpleNamespace(advertisement_status=gatt.ADVERTISEMENT_ABORTED))
    with pytest.raises(gatt.GattAdvertisingError, match="cannot transmit") as error:
        asyncio.run(asyncio.wait_for(instance._async_wait_until_advertising(), 0.5))
    assert error.value.detail_code == "windows_advertising_unavailable"
    assert error.value.terminal_state == "error"


def test_created_provider_timeout_is_receiver_error(monkeypatch):
    monkeypatch.setattr(gatt, "ADVERTISEMENT_START_TIMEOUT_SECONDS", 0.01)
    monkeypatch.setattr(gatt, "ADVERTISEMENT_POLL_SECONDS", 0.001)
    instance = server(SimpleNamespace(advertisement_status=gatt.ADVERTISEMENT_CREATED))
    with pytest.raises(gatt.GattAdvertisingError, match="Pairing has not started"):
        asyncio.run(instance._async_wait_until_advertising())
