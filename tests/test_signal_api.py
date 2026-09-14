"""Exercise real scoped endpoint methods without importing the HA runtime."""

import ast
import asyncio
import copy
import json
import time
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from aiohttp import web
from test_signal_access import access


def endpoint():
    source = (
        Path(__file__).parents[1] / "custom_components/presence_bridge/signal_api.py"
    )
    tree = ast.parse(source.read_text())
    cls = next(n for n in tree.body if isinstance(n, ast.ClassDef))
    ns = {
        "HomeAssistantView": object,
        "web": web,
        "time": time,
        "HEADERS": {"Cache-Control": "no-store"},
        "matches_access": access.matches_access,
        "new_access": access.new_access,
    }
    exec(compile(ast.Module(body=[cls], type_ignores=[]), str(source), "exec"), ns)
    token, record = access.new_access(600)
    c = SimpleNamespace(
        memory={"identities": {"phone": {"monitor_pending": record}}},
        identity_states={"phone": True},
        entry=SimpleNamespace(entry_id="entry"),
        store=SimpleNamespace(async_save=AsyncMock()),
        signal_payload=lambda identity: {
            "label": identity,
            "receivers": [],
            "rssi": None,
        },
    )
    view = ns["SignalView"](lambda _: c)
    request = SimpleNamespace(
        headers={"X-Presence-Monitor": token}, app={"hass": object()}
    )
    return view, c, request


def test_redemption_is_single_use_and_reads_only_selected_phone():
    view, c, request = endpoint()
    with pytest.raises(web.HTTPUnauthorized):
        asyncio.run(view.get(request))
    response = asyncio.run(view.post(request))
    grant = json.loads(response.body)
    with pytest.raises(web.HTTPUnauthorized):
        asyncio.run(view.post(request))
    request.headers["X-Presence-Monitor"] = grant["token"]
    response = asyncio.run(view.get(request))
    assert json.loads(response.body) == {
        "label": "phone",
        "receivers": [],
        "rssi": None,
    }
    assert response.headers["Cache-Control"] == "no-store"
    assert grant["token"] not in str(c.memory)
    with pytest.raises(web.HTTPTooManyRequests):
        asyncio.run(view.get(request))
    c.memory["identities"].pop("phone")
    with pytest.raises(web.HTTPUnauthorized):
        asyncio.run(view.get(request))


def test_missing_wrong_revoked_and_expired_credentials_fail_closed():
    view, c, request = endpoint()
    c.memory["identities"]["phone"]["monitor_pending"]["expires"] = 0
    with pytest.raises(web.HTTPUnauthorized):
        asyncio.run(view.post(request))
    request.headers.clear()
    with pytest.raises(web.HTTPUnauthorized):
        asyncio.run(view.get(request))


def test_new_qr_preserves_existing_grant_until_redemption():
    view, c, request = endpoint()
    grant = json.loads(asyncio.run(view.post(request)).body)
    pending, record = access.new_access(600)
    c.memory["identities"]["phone"]["monitor_pending"] = record
    request.headers["X-Presence-Monitor"] = grant["token"]
    assert asyncio.run(view.get(request)).status == 200
    request.headers["X-Presence-Monitor"] = pending
    replacement = json.loads(asyncio.run(view.post(request)).body)
    request.headers["X-Presence-Monitor"] = grant["token"]
    with pytest.raises(web.HTTPUnauthorized):
        asyncio.run(view.get(request))
    view.last_reads.clear()
    request.headers["X-Presence-Monitor"] = replacement["token"]
    assert asyncio.run(view.get(request)).status == 200


def test_two_phones_cannot_read_each_other_and_revocation_is_scoped():
    view, c, request = endpoint()
    grants = {}
    for identity in ["phone", "other_phone"]:
        token, access_record = access.new_access(90 * 86400)
        grants[identity] = token
        c.memory["identities"][identity] = {"monitor_access": access_record}
        c.identity_states[identity] = True
        request.headers["X-Presence-Monitor"] = token
        assert json.loads(asyncio.run(view.get(request)).body)["label"] == identity
    c.memory["identities"]["phone"].pop("monitor_access")
    request.headers["X-Presence-Monitor"] = grants["phone"]
    with pytest.raises(web.HTTPUnauthorized):
        asyncio.run(view.get(request))
    request.headers["X-Presence-Monitor"] = grants["other_phone"]
    view.last_reads.clear()
    assert json.loads(asyncio.run(view.get(request)).body)["label"] == "other_phone"


def test_service_outage_returns_unavailable_without_revoking_access():
    view, c, request = endpoint()
    grant = json.loads(asyncio.run(view.post(request)).body)
    request.headers["X-Presence-Monitor"] = grant["token"]
    saved = copy.deepcopy(c.memory)

    def unavailable(_hass):
        raise RuntimeError("Integration restarting")

    view.get_coordinator = unavailable
    with pytest.raises(web.HTTPServiceUnavailable):
        asyncio.run(view.get(request))
    assert c.memory == saved
    # A reloaded service uses persisted hashes, not a new pairing invitation.
    view.get_coordinator = lambda _hass: c
    c.memory = json.loads(json.dumps(saved))
    assert asyncio.run(view.get(request)).status == 200


def test_concurrent_redemption_only_issues_one_grant():
    view, c, request = endpoint()

    async def exercise():
        return await asyncio.gather(
            view.post(request), view.post(request), return_exceptions=True
        )

    results = asyncio.run(exercise())
    assert sum(isinstance(item, web.HTTPUnauthorized) for item in results) == 1
    assert sum(getattr(item, "status", None) == 200 for item in results) == 1
    c.store.async_save.assert_awaited_once()
