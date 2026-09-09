"""Tests for automatic old/new iPhone pairing transport selection."""

from __future__ import annotations

import asyncio
import json
import time
from pathlib import Path

import interactive_pairing_helper as helper
import pytest
from protocol import PairingLink
from reverse_gatt_client import ReverseGattResult


def command(
    link: PairingLink,
    *,
    transport: str = "iphone_peripheral",
) -> dict[str, object]:
    return {
        "schema": 1,
        "session_id": link.session_id,
        "pairing_uri": link.to_uri(),
        "timeout_seconds": 60,
        "transport": transport,
        "gatt": {
            "service_uuid": "service",
            "session_uuid": "session",
            "claim_uuid": "claim",
            "result_uuid": "result",
        },
    }


def link() -> PairingLink:
    return PairingLink(
        session_id="abcdefghijklmnopQRSTUVWX",
        observer_id="dell_cucina",
        expires_at=int(time.time()) + 300,
        secret=bytes(range(32)),
    )


def test_legacy_transport_can_win_automatic_pairing(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class Reverse:
        detail_code = "waiting_for_iphone_advertisement"

        def __init__(self, **_kwargs: object) -> None:
            pass

        async def async_pair(
            self, _link: PairingLink, _timeout: int
        ) -> ReverseGattResult:
            await asyncio.Event().wait()
            raise AssertionError("cancelled task resumed")

    class Legacy:
        def __init__(self, **_kwargs: object) -> None:
            self.stopped = False

        async def async_start(self, _link: PairingLink) -> None:
            return None

        async def async_wait_for_claim(self, _timeout: int) -> dict[str, bool]:
            return {"claim_verified": True}

        async def async_stop(self) -> None:
            self.stopped = True

    monkeypatch.setattr(helper, "ReverseGattPairingClient", Reverse)
    monkeypatch.setattr(helper, "GattPairingServer", Legacy)
    command_path = tmp_path / "command.json"
    result_path = tmp_path / "result.json"
    command_path.write_text(
        json.dumps(command(link(), transport="windows_peripheral")),
        encoding="utf-8",
    )

    assert asyncio.run(helper._run(command_path, result_path)) == 0
    result = json.loads(result_path.read_text(encoding="utf-8"))
    assert result["state"] == "success"
    assert result["transport"] == "windows_peripheral"
    assert not command_path.exists()


def test_current_transport_waits_for_proximity_then_pairs(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    proximity_stopped = asyncio.Event()

    class Reverse:
        detail_code = "iphone_claim_accepted"

        def __init__(self, **_kwargs: object) -> None:
            pass

        async def async_pair(
            self, _link: PairingLink, _timeout: int
        ) -> ReverseGattResult:
            await proximity_stopped.wait()
            return ReverseGattResult(
                address="AA:BB:CC:DD:EE:FF",
                name="Presence Pair",
            )

    class Legacy:
        def __init__(self, **_kwargs: object) -> None:
            raise AssertionError("legacy server must not reserve the adapter")

    class Proximity:
        stopped = False

        def __init__(self, **_kwargs: object) -> None:
            pass

        async def async_start(self, _link: PairingLink) -> None:
            return None

        async def async_wait_until_ready(
            self, _timeout: int
        ) -> dict[str, bool]:
            return {"claim_verified": True}

        async def async_stop(self) -> None:
            self.stopped = True
            proximity_stopped.set()

    monkeypatch.setattr(helper, "ReverseGattPairingClient", Reverse)
    monkeypatch.setattr(helper, "GattPairingServer", Legacy)
    monkeypatch.setattr(helper, "GattProximityServer", Proximity)
    command_path = tmp_path / "command.json"
    result_path = tmp_path / "result.json"
    command_path.write_text(json.dumps(command(link())), encoding="utf-8")

    assert asyncio.run(
        asyncio.wait_for(helper._run(command_path, result_path), timeout=2)
    ) == 0
    result = json.loads(result_path.read_text(encoding="utf-8"))
    assert result["state"] == "success"
    assert result["transport"] == "iphone_peripheral"
    assert proximity_stopped.is_set()


def test_current_transport_keeps_direct_path_for_existing_app_builds(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    proximity_stopped = False

    class Reverse:
        detail_code = "iphone_claim_accepted"

        def __init__(self, **_kwargs: object) -> None:
            pass

        async def async_pair(
            self, _link: PairingLink, _timeout: int
        ) -> ReverseGattResult:
            return ReverseGattResult(
                address="AA:BB:CC:DD:EE:FF",
                name="Presence Pair",
            )

    class Proximity:
        def __init__(self, **_kwargs: object) -> None:
            pass

        async def async_start(self, _link: PairingLink) -> None:
            return None

        async def async_wait_until_ready(
            self, _timeout: int
        ) -> dict[str, bool]:
            await asyncio.Event().wait()
            raise AssertionError("cancelled proximity task resumed")

        async def async_stop(self) -> None:
            nonlocal proximity_stopped
            proximity_stopped = True

    monkeypatch.setattr(helper, "ReverseGattPairingClient", Reverse)
    monkeypatch.setattr(helper, "GattProximityServer", Proximity)
    command_path = tmp_path / "command.json"
    result_path = tmp_path / "result.json"
    command_path.write_text(json.dumps(command(link())), encoding="utf-8")

    assert asyncio.run(
        asyncio.wait_for(helper._run(command_path, result_path), timeout=2)
    ) == 0
    assert proximity_stopped


def test_proximity_provider_is_recreated_after_delayed_windows_release(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    attempts = 0
    stopped = 0

    class Proximity:
        def __init__(self, **_kwargs: object) -> None:
            pass

        async def async_start(self, _link: PairingLink) -> None:
            nonlocal attempts
            attempts += 1
            if attempts < 3:
                raise RuntimeError("adapter still releasing")

        async def async_stop(self) -> None:
            nonlocal stopped
            stopped += 1

    async def no_sleep(_seconds: float) -> None:
        return None

    monkeypatch.setattr(helper, "GattProximityServer", Proximity)
    monkeypatch.setattr(helper.asyncio, "sleep", no_sleep)
    result_path = tmp_path / "result.json"

    server = asyncio.run(helper._start_proximity_server(link(), result_path))

    assert isinstance(server, Proximity)
    assert attempts == 3
    assert stopped == 2
    result = json.loads(result_path.read_text(encoding="utf-8"))
    assert result["state"] == "progress"
    assert result["detail_code"] == "windows_adapter_recovering"


def test_command_secret_is_removed_before_parsing(tmp_path: Path) -> None:
    command_path = tmp_path / "command.json"
    result_path = tmp_path / "result.json"
    command_path.write_text("not-json", encoding="utf-8")

    with pytest.raises(json.JSONDecodeError):
        asyncio.run(helper._run(command_path, result_path))

    assert not command_path.exists()


def test_result_write_retries_transient_windows_lock(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    result_path = tmp_path / "result.json"
    original_replace = Path.replace
    attempts = 0

    def flaky_replace(source: Path, target: Path) -> Path:
        nonlocal attempts
        attempts += 1
        if attempts < 3:
            raise PermissionError("sharing violation")
        return original_replace(source, target)

    monkeypatch.setattr(Path, "replace", flaky_replace)
    monkeypatch.setattr(helper.time, "sleep", lambda _seconds: None)

    helper._write_json(result_path, {"state": "progress"})

    assert attempts == 3
    assert json.loads(result_path.read_text(encoding="utf-8")) == {
        "state": "progress"
    }
    assert not list(tmp_path.glob("*.tmp"))
