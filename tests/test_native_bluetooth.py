"""Native HA scanners remain passive and never refresh stale advertisements."""

import ast
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

from test_presence_freshness import make_coordinator


def setup_native():
    c, _, _ = make_coordinator()
    source = (
        Path(__file__).parents[1]
        / "custom_components/presence_bridge/native_bluetooth.py"
    )
    tree = ast.parse(source.read_text())
    scanner = SimpleNamespace(source="AA:BB:CC:DD:EE:00", name="Proxy", scanning=True)
    info = SimpleNamespace(
        source=scanner.source,
        address="AA:BB:CC:DD:EE:FF",
        rssi=-60,
        time=time.monotonic() - 20,
    )
    bt = SimpleNamespace(
        async_current_scanners=Mock(return_value=[scanner]),
        async_discovered_service_info=Mock(return_value=[info]),
    )
    registry = SimpleNamespace(
        async_get_device=lambda **_: SimpleNamespace(
            name_by_user=None, name="Living room receiver", area_id="living_room"
        )
    )
    ns = {
        "time": time,
        "UTC": UTC,
        "datetime": datetime,
        "timedelta": timedelta,
        "bluetooth": bt,
        "dr": SimpleNamespace(
            async_get=lambda _: registry, CONNECTION_BLUETOOTH="bluetooth"
        ),
    }
    exec(
        compile(
            ast.Module(
                body=[n for n in tree.body if isinstance(n, ast.FunctionDef)],
                type_ignores=[],
            ),
            str(source),
            "exec",
        ),
        ns,
    )
    c.hass = SimpleNamespace(config=SimpleNamespace(components={"bluetooth"}))

    def ensure(oid, payload):
        if oid not in c.observers:
            c.observers[oid] = SimpleNamespace(
                observer_id=oid, area_id=None, capabilities=[], last_seen=None
            )
        return c.observers[oid]

    c._ensure_observer = ensure
    return c, ns["refresh_native_observations"], bt, scanner, info


def test_native_passive_receiver_uses_actual_advertisement_timestamp():
    c, refresh, bt, scanner, info = setup_native()
    refresh(c)
    o = next(iter(c.observers.values()))
    assert o.online and o.area_id == "living_room"
    assert "app_pairing" not in o.capabilities
    seen = datetime.fromisoformat(o.observations[0]["seen_at"])
    assert 19 < (datetime.now(UTC) - seen).total_seconds() < 22
    refresh(c)
    assert (
        abs(
            (
                datetime.fromisoformat(o.observations[0]["seen_at"]) - seen
            ).total_seconds()
        )
        < 0.1
    )
    bt.async_discovered_service_info.assert_called_with(c.hass, False)


def test_explicit_area_is_preserved_and_removed_receiver_expires():
    c, refresh, bt, scanner, _ = setup_native()
    refresh(c)
    o = next(iter(c.observers.values()))
    c.memory["observer_settings"] = {o.observer_id: {"area_id": "office"}}
    o.area_id = "office"
    refresh(c)
    assert o.area_id == "office"
    bt.async_current_scanners.return_value = []
    refresh(c)
    assert not o.online and o.last_seen is None and not o.observations


def test_stale_future_or_offline_receiver_packets_are_ignored():
    for age in (300, -50):
        c, refresh, _, scanner, info = setup_native()
        info.time = time.monotonic() - age
        refresh(c)
        assert not next(iter(c.observers.values())).observations
    c, refresh, _, scanner, info = setup_native()
    scanner.scanning = False
    refresh(c)
    o = next(iter(c.observers.values()))
    assert not o.online and not o.observations and o.last_seen is None
