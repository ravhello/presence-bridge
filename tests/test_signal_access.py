"""Signal codes do not grant HA control or Bluetooth key access."""

import importlib.util
from pathlib import Path

import pytest

spec = importlib.util.spec_from_file_location(
    "signal_access",
    Path(__file__).parents[1] / "custom_components/presence_bridge/signal_access.py",
)
access = importlib.util.module_from_spec(spec)
spec.loader.exec_module(access)


def test_access_is_hashed_scoped_and_expires():
    token, record = access.new_access(600)
    other, _ = access.new_access(600)
    assert token not in str(record)
    assert access.matches_access(token, record)
    assert not access.matches_access(other, record)
    assert not access.matches_access(token, {**record, "expires": 0})
    assert not access.matches_access(token, None)
    assert not access.matches_access("x" * 10000, record)


@pytest.mark.parametrize(
    "origin",
    [
        "http://ha.local:8123",
        "https://u:p@ha.local",
        "https://ha.local/a",
        "https://ha.local?q=secret",
        "https://localhost",
        "https://127.0.0.1",
        "https://ha.local/#secret",
    ],
)
def test_monitor_origin_rejects_unsafe_or_unreachable_forms(origin):
    with pytest.raises(ValueError):
        access.monitor_origin(origin)


def test_monitor_origin_accepts_https_lan():
    assert (
        access.monitor_origin("https://192.168.0.121:8123/")
        == "https://192.168.0.121:8123"
    )
