"""Service registration contract and real HA permission-boundary regressions."""

from __future__ import annotations

import ast
import asyncio
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

ROOT = Path(__file__).parents[1]
SOURCE = ROOT / "custom_components/presence_bridge/__init__.py"
SERVICES = {
    "start_pairing": {
        "person": "person.test",
        "force_new": False,
        "timeout_seconds": 60,
    },
    "cancel_pairing": {},
    "set_observer_area": {"observer_id": "test_receiver", "area_id": "office"},
    "remove_identity": {"identity_id": "test_phone"},
}


def load_registration(namespace):
    tree = ast.parse(SOURCE.read_text(encoding="utf-8"))
    registration = next(
        node
        for node in tree.body
        if isinstance(node, ast.FunctionDef) and node.name == "_async_register_services"
    )
    exec(
        compile(ast.Module(body=[registration], type_ignores=[]), str(SOURCE), "exec"),
        namespace,
    )
    return namespace["_async_register_services"]


def test_every_service_uses_the_official_admin_helper():
    tree = ast.parse(SOURCE.read_text(encoding="utf-8"))
    assert any(
        isinstance(node, ast.ImportFrom)
        and node.module == "homeassistant.helpers.service"
        and any(alias.name == "async_register_admin_service" for alias in node.names)
        for node in tree.body
    )
    register = Mock()
    hass = SimpleNamespace(services=SimpleNamespace(async_register=Mock()))
    response = SimpleNamespace(OPTIONAL="optional")
    namespace = {
        "callback": lambda fn: fn,
        "async_register_admin_service": register,
        "DOMAIN": "presence_bridge",
        "SupportsResponse": response,
        "START_PAIRING_SCHEMA": object(),
        "SET_OBSERVER_AREA_SCHEMA": object(),
        "REMOVE_IDENTITY_SCHEMA": object(),
    }
    load_registration(namespace)(hass)
    hass.services.async_register.assert_not_called()
    assert {call.args[2] for call in register.call_args_list} == set(SERVICES)
    assert register.call_count == len(SERVICES)
    for call in register.call_args_list:
        assert call.args[:2] == (hass, "presence_bridge")
        if call.args[2] in {"start_pairing", "cancel_pairing"}:
            assert call.kwargs["supports_response"] == response.OPTIONAL


def test_bluetooth_is_a_required_dependency_not_an_optional_import():
    manifest = json.loads(
        (ROOT / "custom_components/presence_bridge/manifest.json").read_text()
    )
    assert "bluetooth" in manifest["dependencies"]
    assert "bluetooth" not in manifest["after_dependencies"]


def real_ha_namespace(coordinator):
    pytest.importorskip("homeassistant", reason="Runs in the HA permissions CI job")
    from homeassistant.core import SupportsResponse
    from homeassistant.helpers.service import async_register_admin_service

    return {
        "callback": lambda fn: fn,
        "async_register_admin_service": async_register_admin_service,
        "DOMAIN": "presence_bridge",
        "SupportsResponse": SupportsResponse,
        "START_PAIRING_SCHEMA": lambda data: data,
        "SET_OBSERVER_AREA_SCHEMA": lambda data: data,
        "REMOVE_IDENTITY_SCHEMA": lambda data: data,
        "_coordinator": lambda _hass: coordinator,
    }


@pytest.mark.parametrize("service", SERVICES)
@pytest.mark.parametrize("user", ["limited", "unknown"])
def test_real_ha_rejects_non_admin_before_any_coordinator_access(
    tmp_path, service, user
):
    namespace = real_ha_namespace(None)
    from homeassistant.core import Context, HomeAssistant
    from homeassistant.exceptions import Unauthorized, UnknownUser

    async def exercise():
        hass = HomeAssistant(str(tmp_path))
        hass.auth = SimpleNamespace(
            async_get_user=AsyncMock(
                return_value=None
                if user == "unknown"
                else SimpleNamespace(is_admin=False)
            )
        )
        get_coordinator = Mock(
            side_effect=AssertionError("Unauthorized coordinator access")
        )
        namespace["_coordinator"] = get_coordinator
        load_registration(namespace)(hass)
        with pytest.raises(UnknownUser if user == "unknown" else Unauthorized):
            await hass.services.async_call(
                "presence_bridge",
                service,
                SERVICES[service],
                blocking=True,
                context=Context(user_id=user),
                return_response=service in {"start_pairing", "cancel_pairing"},
            )
        get_coordinator.assert_not_called()
        await hass.async_block_till_done()
        await hass.async_stop(force=True)

    asyncio.run(exercise())


@pytest.mark.parametrize("service", SERVICES)
@pytest.mark.parametrize("user", ["admin", None])
@pytest.mark.parametrize("request_response", [True, False])
def test_real_ha_allows_admin_and_internal_automation_calls(
    tmp_path, service, user, request_response
):
    payload = {"pairing_uri": "test-only-invitation", "qr_data_uri": "test-only-qr"}
    coordinator = SimpleNamespace(
        async_start_pairing=AsyncMock(return_value=payload),
        async_cancel_pairing=AsyncMock(),
        pairing_payload=Mock(return_value=payload),
        async_set_observer_area=AsyncMock(),
        async_remove_identity=AsyncMock(),
    )
    namespace = real_ha_namespace(coordinator)
    from homeassistant.core import Context, HomeAssistant

    async def exercise():
        hass = HomeAssistant(str(tmp_path))
        hass.auth = SimpleNamespace(
            async_get_user=AsyncMock(return_value=SimpleNamespace(is_admin=True))
        )
        load_registration(namespace)(hass)
        has_response = request_response and service in {
            "start_pairing",
            "cancel_pairing",
        }
        response = await hass.services.async_call(
            "presence_bridge",
            service,
            SERVICES[service],
            blocking=True,
            context=Context(user_id=user),
            return_response=has_response,
        )
        assert response == (payload if has_response else None)
        handler = getattr(coordinator, f"async_{service}")
        handler.assert_awaited_once()
        if service == "start_pairing":
            handler.assert_awaited_once_with("person.test", None, 60, force_new=False)
        elif service == "cancel_pairing":
            handler.assert_awaited_once_with(publish=True)
        elif service == "set_observer_area":
            handler.assert_awaited_once_with("test_receiver", "office")
        else:
            handler.assert_awaited_once_with("test_phone")
        if user is None:
            hass.auth.async_get_user.assert_not_awaited()
        await hass.async_block_till_done()
        await hass.async_stop(force=True)

    asyncio.run(exercise())
