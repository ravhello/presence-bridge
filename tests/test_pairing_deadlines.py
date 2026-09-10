"""Exercise HA's actual status handler without starting Home Assistant."""

import ast
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import Mock

import pytest


@pytest.fixture
def coordinator():
    source = (
        Path(__file__).parents[1] / "custom_components/presence_bridge/coordinator.py"
    )
    tree = ast.parse(source.read_text(encoding="utf-8"))
    selected = []
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(
            isinstance(target, ast.Name)
            and target.id
            in {
                "_ACTIVE_PAIRING_STATES",
                "_PAIRING_HANDOFF_CODES",
                "_PAIRING_COMPLETION_CODES",
            }
            for target in node.targets
        ):
            selected.append(node)
        if isinstance(node, ast.FunctionDef) and node.name == "_bounded_lease_deadline":
            selected.append(node)
        if isinstance(node, ast.ClassDef) and node.name == "PresenceBridgeCoordinator":
            method = next(
                item
                for item in node.body
                if getattr(item, "name", "") == "_pairing_status_message"
            )
            method.decorator_list = []
            selected.append(method)
    clock = SimpleNamespace(time=lambda: 1000)
    namespace = {
        "Any": Any,
        "time": clock,
        "PAIRING_HANDOFF_TIMEOUT": 300,
        "PAIRING_COMPLETION_TIMEOUT": 300,
    }
    exec(
        compile(ast.Module(body=selected, type_ignores=[]), str(source), "exec"),
        namespace,
    )
    obj = SimpleNamespace(
        _pairing_session={
            "session_id": "same_session_1234",
            "observer_id": "dell",
            "expires_at": 1100,
        },
        observers={},
        _decode_payload=lambda message: message,
        _set_pairing_state=Mock(),
    )

    def send(now, code, **extra):
        clock.time = lambda: now
        namespace["_pairing_status_message"](
            obj,
            {
                "session_id": "same_session_1234",
                "observer_id": "dell",
                "state": "connecting",
                "detail_code": code,
                **extra,
            },
        )

    return obj, send


def test_repeated_progress_never_renews_attempt(coordinator):
    obj, send = coordinator
    send(1000, "receiver_proximity_confirmed")
    send(1200, "receiver_proximity_confirmed")
    assert obj._pairing_session["attempt_expires_at"] == 1300
    send(1250, "iphone_session_verified", completion_expires_at=1550)
    assert obj._pairing_session["completion_expires_at"] == 1300
    send(1290, "identity_captured", completion_expires_at=1590)
    assert obj._pairing_session["completion_expires_at"] == 1300


def test_completion_without_handoff_has_only_five_minutes(coordinator):
    obj, send = coordinator
    send(1000, "iphone_claim_accepted")
    send(1100, "identity_captured")
    assert obj._pairing_session["completion_expires_at"] == 1300


def test_other_session_cannot_change_deadline(coordinator):
    obj, send = coordinator
    send(1000, "receiver_proximity_confirmed", session_id="old_session_1234")
    assert "attempt_expires_at" not in obj._pairing_session
    obj._set_pairing_state.assert_not_called()
