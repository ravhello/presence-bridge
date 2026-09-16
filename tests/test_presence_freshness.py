"""Exercise real coordinator methods with clocks and receivers under control."""

import ast
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import Mock

ROOT = Path(__file__).parents[1] / "custom_components/presence_bridge"


def make_coordinator():
    now = datetime.now(UTC)
    ns = {"Any": Any, "datetime": datetime, "dataclass": dataclass, "field": field}
    tree = ast.parse((ROOT / "models.py").read_text())
    exec(
        compile(
            ast.Module(
                body=[n for n in tree.body if isinstance(n, ast.ClassDef)],
                type_ignores=[],
            ),
            "models",
            "exec",
        ),
        ns,
    )
    names = {
        "_resolve_identities",
        "_matches_for_irk",
        "_expire_runtime_state",
        "_normalize_observations",
        "identity_payload",
        "signal_payload",
        "_status_message",
    }
    tree = ast.parse((ROOT / "coordinator.py").read_text())
    cls = next(n for n in tree.body if isinstance(n, ast.ClassDef))
    cls.body = [n for n in cls.body if getattr(n, "name", None) in names]
    for n in cls.body:
        if n.name != "_normalize_observations":
            n.decorator_list = []
    ns.update(
        {
            "_utcnow": lambda: now,
            "_parse_timestamp": lambda value: datetime.fromisoformat(value)
            if value
            else None,
            "get_cipher_for_irk": Mock(return_value="cipher"),
            "resolve_private_address": lambda cipher, address: address
            == "AA:BB:CC:DD:EE:FF",
            "MIN_RSSI": -127,
            "DOMAIN": "presence_bridge",
            "_IRK_RE": __import__("re").compile("[A-F0-9]{32}"),
            "_observer_id_from_topic": lambda topic: "dell",
            "async_dispatcher_send": Mock(),
            "SIGNAL_STATE_UPDATED": "updated",
        }
    )
    exec(compile(ast.Module(body=[cls], type_ignores=[]), "coordinator", "exec"), ns)
    c = ns["PresenceBridgeCoordinator"]()
    c.away_timeout = 180
    c.observer_timeout = 90
    c._cipher_cache = {}
    c.memory = {"identities": {"phone": {"irk": "11" * 16}}}
    c.identity_states = {"phone": ns["IdentityState"]("phone", "person.test", "Phone")}
    c.observers = {}
    c.area_registry = SimpleNamespace(
        async_get_area=lambda _: SimpleNamespace(name="Room")
    )
    c.hass = object()
    c._decode_payload = lambda msg: msg.payload
    c._ensure_observer = lambda oid, payload: c.observers[oid]
    return c, ns, now


def add_observer(c, ns, now, oid="dell", age=0, rssi=-60):
    o = ns["ObserverState"](oid, oid, online=True, area_id=oid, last_seen=now)
    o.observations = [
        {
            "address": "AA:BB:CC:DD:EE:FF",
            "rssi": rssi,
            "seen_at": (now - timedelta(seconds=age)).isoformat(),
        }
    ]
    c.observers[oid] = o
    return o


def test_replayed_observation_does_not_refresh_last_seen():
    c, ns, now = make_coordinator()
    add_observer(c, ns, now, age=60)
    c._resolve_identities()
    assert c.identity_states["phone"].last_seen == now - timedelta(seconds=60)
    c._resolve_identities()
    assert c.identity_states["phone"].last_seen == now - timedelta(seconds=60)
    assert c.identity_payload("phone")["room_fresh"] is False


def test_bluez_resolved_address_is_scoped_to_enrolling_receiver():
    c, ns, now = make_coordinator()
    peer = "11:22:33:44:55:66"
    c.memory["identities"]["phone"].update(identity_address=peer, paired_by="linux_owner")
    foreign = add_observer(c, ns, now, oid="foreign")
    foreign.observations[0]["address"] = peer
    c._resolve_identities()
    assert not c.identity_states["phone"].is_home
    owner = add_observer(c, ns, now, oid="linux_owner")
    owner.observations[0]["address"] = peer
    c._resolve_identities()
    assert c.identity_states["phone"].is_home
    assert c.identity_states["phone"].observer_id == "linux_owner"
    owner.online = False
    assert c._matches_for_irk("11" * 16) == []


def test_old_future_and_unknown_timestamp_cannot_mark_home():
    for age in (181, -30):
        c, ns, now = make_coordinator()
        o = add_observer(c, ns, now, age=age)
        c._resolve_identities()
        assert not c.identity_states["phone"].is_home
        o.observations[0]["seen_at"] = ""
        c._resolve_identities()
        assert not c.identity_states["phone"].is_home


def test_offline_message_clears_cache_and_cannot_resurrect_on_timer():
    c, ns, now = make_coordinator()
    o = add_observer(c, ns, now)
    c._status_message(
        SimpleNamespace(
            topic="status", payload={"online": False, "timestamp": now.isoformat()}
        )
    )
    c._expire_runtime_state()
    assert not o.online
    assert not o.observations
    c._resolve_identities()
    assert not c.identity_states["phone"].is_home


def test_room_hysteresis_and_freshness_over_old_strong_signal():
    c, ns, now = make_coordinator()
    add_observer(c, ns, now, oid="a", rssi=-60)
    c._resolve_identities()
    add_observer(c, ns, now, oid="b", rssi=-57)
    c._resolve_identities()
    assert c.identity_states["phone"].observer_id == "a"
    c.observers["b"].observations[0]["rssi"] = -50
    c._resolve_identities()
    assert c.identity_states["phone"].observer_id == "b"
    c.observers["b"].observations[0]["seen_at"] = (
        now - timedelta(seconds=90)
    ).isoformat()
    c._resolve_identities()
    assert c.identity_states["phone"].observer_id == "a"


def test_away_payload_has_no_current_room():
    c, ns, now = make_coordinator()
    add_observer(c, ns, now)
    c._resolve_identities()
    c.identity_states["phone"].last_seen = now - timedelta(seconds=181)
    c._expire_runtime_state()
    payload = c.identity_payload("phone")
    assert payload["area_name"] is None
    assert payload["observer_name"] is None
    assert payload["presence_evidence"] == "not_detected"
    assert payload["last_known_area"] == "Room"


def test_latest_rotated_address_replaces_older_strong_rssi():
    c, ns, now = make_coordinator()
    o = add_observer(c, ns, now, age=20, rssi=-40)
    o.observations.append(
        {"address": "AA:BB:CC:DD:EE:FF", "rssi": -85, "seen_at": now.isoformat()}
    )
    c._resolve_identities()
    assert c.signal_payload("phone")["rssi"] == -85


def test_signal_uses_its_own_timestamp_and_only_matching_receivers():
    c, ns, now = make_coordinator()
    add_observer(c, ns, now, age=50, rssi=-50)
    c._resolve_identities()
    add_observer(c, ns, now, oid="new", age=25, rssi=-53)
    add_observer(c, ns, now, oid="unrelated").observations.clear()
    c._resolve_identities()
    p = c.identity_payload("phone")
    assert p["last_seen"] == (now - timedelta(seconds=25)).isoformat()
    assert p["rssi"] is None
    assert p["room_fresh"] is False
    assert p["receiver_count"] == 1
    assert p["signal_receivers"][0]["observer_id"] == "new"


def test_stale_signal_disappears_before_presence_timeout():
    c, ns, now = make_coordinator()
    add_observer(c, ns, now, age=46)
    c._resolve_identities()
    assert c.identity_payload("phone")["is_home"]
    assert c.signal_payload("phone")["rssi"] is None
    assert c.signal_payload("phone")["distance_m"] is None
