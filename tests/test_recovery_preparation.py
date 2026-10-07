"""Run actual coordinator transitions without a live radio or Home Assistant."""

import ast
import asyncio
import base64
import hashlib
import json
import os
import re
import secrets
import time
from datetime import UTC, datetime
from functools import wraps
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock
from urllib.parse import parse_qs, urlencode, urlsplit

import pytest
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding


@pytest.fixture
def coordinator():
    path = Path(
        os.environ.get(
            "PRESENCE_COORDINATOR_SOURCE",
            Path(__file__).parents[1]
            / "custom_components/presence_bridge/coordinator.py",
        )
    )
    tree = ast.parse(path.read_text(encoding="utf-8"))
    names = {
        "async_setup",
        "_async_start_pairing_locked",
        "async_cancel_pairing",
        "_set_pairing_state",
        "_async_process_pairing_result",
        "_async_require_phone_reset",
    }
    cls = next(n for n in tree.body if isinstance(n, ast.ClassDef))
    cls.body = [n for n in cls.body if getattr(n, "name", None) in names]
    constants = [
        n
        for n in tree.body
        if isinstance(n, ast.Assign | ast.FunctionDef)
        and (
            getattr(n, "name", "") == "_pairing_deadline"
            or any(
                getattr(t, "id", "")
                in {"_ACTIVE_PAIRING_STATES", "_FORCED_RENEWAL_COALESCE_SECONDS"}
                for t in getattr(n, "targets", [])
            )
        )
    ]
    private_key = Mock()
    private_key.public_key.return_value.public_bytes.return_value = b"test_public"
    ns = {
        "time": time,
        "json": json,
        "base64": base64,
        "secrets": secrets,
        "hashlib": hashlib,
        "re": re,
        "rsa": SimpleNamespace(generate_private_key=lambda **_: private_key),
        "serialization": serialization,
        "padding": padding,
        "hashes": hashes,
        "urlencode": urlencode,
        "HomeAssistantError": RuntimeError,
        "MIN_PAIRING_TIMEOUT": 60,
        "MAX_PAIRING_TIMEOUT": 600,
        "PAIRING_HANDOFF_TIMEOUT": 300,
        "PAIRING_COMPLETION_TIMEOUT": 300,
        "PairingLink": lambda **_: SimpleNamespace(
            to_uri=lambda: "presencepair://pair?v=2"
        ),
        "b64url_encode": lambda _: "test_secret",
        "TOPIC_ROOT": "observers",
        "GATT_SERVICE_UUID": "service",
        "GATT_SESSION_UUID": "session",
        "GATT_CLAIM_UUID": "claim",
        "GATT_RESULT_UUID": "result",
        "dt_util": SimpleNamespace(now=lambda: datetime.now(UTC)),
        "async_dispatcher_send": Mock(),
        "SIGNAL_STATE_UPDATED": "updated",
        "_IRK_RE": re.compile(r"^[0-9A-F]{32}$"),
        "DOMAIN": "presence_bridge",
        "refresh_native_observations": Mock(),
    }
    for topic in (
        "TOPIC_STATUS",
        "TOPIC_OBSERVATIONS",
        "TOPIC_PAIRING_STATUS",
        "TOPIC_PAIRING_RESULT",
        "TOPIC_IDENTITY_REMOVAL_RESULT",
    ):
        ns[topic] = topic
    future = ast.parse("from __future__ import annotations").body
    exec(
        compile(
            ast.Module(body=future + constants + [cls], type_ignores=[]),
            str(path),
            "exec",
        ),
        ns,
    )
    c = ns["PresenceBridgeCoordinator"]()
    c.memory = {"identities": {"preserved": {"irk": "22" * 16}}}
    c.store = SimpleNamespace(async_save=AsyncMock())
    c._pairing_session = None
    c.pairing_public = {"state": "idle"}
    c._select_observer = lambda oid: SimpleNamespace(
        observer_id=oid or "dell", name="Receiver"
    )

    async def executor(fn, *args):
        return fn(*args)

    c.hass = SimpleNamespace(
        states=SimpleNamespace(get=lambda _: SimpleNamespace(name="Test")),
        async_add_executor_job=executor,
        config_entries=SimpleNamespace(async_entries=lambda _: ["mqtt"]),
        async_create_background_task=lambda coroutine, _: coroutine.close(),
    )
    c.entry = SimpleNamespace(options={})
    c._unsubscribers = []
    c._async_periodic_refresh = AsyncMock()
    for handler in (
        "_status_message",
        "_observations_message",
        "_pairing_status_message",
        "_pairing_result_message",
        "_identity_removal_result_message",
    ):
        setattr(c, handler, Mock())
    c._qr_data_uri = lambda uri: "qr:" + uri
    c._async_publish = AsyncMock()
    # Live legacy installation publishes directly; exercise the same transitions.
    ns["mqtt"] = SimpleNamespace(
        async_publish=c._async_publish, async_subscribe=AsyncMock()
    )
    c.pairing_payload = lambda: dict(c.pairing_public)
    c._cipher_cache = {}
    c._rebuild_identity_states = Mock()
    c._resolve_identities = Mock()
    c._matches_for_irk = Mock(return_value=[("dell", -60)])
    return c, private_key


async def start(c, person="person.test", observer="dell"):
    return await c._async_start_pairing_locked(person, observer, 600, force_new=True)


def run_async(test):
    @wraps(test)
    def execute(*args, **kwargs):
        return asyncio.run(test(*args, **kwargs))

    return execute


@run_async
async def test_normal_retry_preserves_identity_and_has_no_forget_instruction(
    coordinator,
):
    c, _ = coordinator
    first = await start(c)
    await c.async_cancel_pairing(publish=True)
    assert c._pairing_session is None
    assert "pairing_uri" not in c.pairing_public
    second = await start(c)
    assert not first["phone_bond_reset_required"]
    assert not second["phone_bond_reset_required"]
    assert "reset" not in parse_qs(urlsplit(second["pairing_uri"]).query)
    assert "preserved" in c.memory["identities"]
    assert [
        json.loads(call.args[2])["action"] for call in c._async_publish.await_args_list
    ] == ["start_app_pairing", "cancel", "start_app_pairing"]


@run_async
async def test_verified_reset_survives_renewal_and_is_target_scoped(coordinator):
    c, _ = coordinator
    await c._async_require_phone_reset("person.test", "dell")
    for _ in range(2):
        data = await start(c)
        assert data["phone_bond_reset_required"]
        assert parse_qs(urlsplit(data["pairing_uri"]).query)["reset"] == [
            "forget_receiver"
        ]
        c._set_pairing_state("waiting_for_app", "Waiting")
        assert c.pairing_public["phone_bond_reset_required"]
        await c.async_cancel_pairing(publish=True)
    for person, receiver in [("person.other", "dell"), ("person.test", "linux")]:
        assert not (await start(c, person, receiver))["phone_bond_reset_required"]
        await c.async_cancel_pairing(publish=True)
    assert c.memory["phone_bond_resets"] == {"person.test:dell": True}


@pytest.mark.parametrize("stored_resets", [{"person.test:dell": True}, None, []])
@run_async
async def test_setup_reloads_phone_preparation_after_restart(
    coordinator, stored_resets
):
    c, _ = coordinator
    stored = {
        "identities": dict(c.memory["identities"]),
        "observer_settings": {"dell": {"area_id": "kitchen"}},
        "phone_bond_resets": stored_resets,
    }
    c.store.async_load = AsyncMock(return_value=stored)
    c.memory = {}
    await c.async_setup()
    expected = stored_resets if isinstance(stored_resets, dict) else {}
    assert c.memory["phone_bond_resets"] == expected
    assert c.memory["identities"] == stored["identities"]
    assert c.memory["observer_settings"] == stored["observer_settings"]
    assert (await start(c))["phone_bond_reset_required"] is bool(expected)


@pytest.mark.parametrize("valid", [False, True])
@run_async
async def test_only_verified_identity_commit_clears_reset_marker(coordinator, valid):
    c, private_key = coordinator
    await c._async_require_phone_reset("person.test", "dell")
    await c._async_require_phone_reset("person.other", "dell")
    await start(c)
    session = c._pairing_session
    private_key.decrypt.return_value = json.dumps(
        {"irk": "11" * 16, "claim_verified": valid, "secure_exchange_complete": valid}
    ).encode()
    await c._async_process_pairing_result(
        {
            "session_id": session["session_id"],
            "observer_id": "dell",
            "ciphertext": "dGVzdA==",
        }
    )
    assert c._pairing_session is None
    assert c.memory["phone_bond_resets"].get("person.test:dell", False) is not valid
    assert c.memory["phone_bond_resets"]["person.other:dell"]
    assert "preserved" in c.memory["identities"]
    assert "qr_data_uri" not in c.pairing_public
