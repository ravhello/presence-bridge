"""Presence Bridge integration."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import homeassistant.helpers.config_validation as cv
import voluptuous as vol
from homeassistant.components import frontend, websocket_api
from homeassistant.components.http import StaticPathConfig
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant, ServiceCall, SupportsResponse, callback

from .const import (
    DEFAULT_PAIRING_TIMEOUT,
    DOMAIN,
    PANEL_ICON,
    PANEL_TITLE,
    PANEL_URL,
    PLATFORMS,
    STATIC_URL,
)
from .coordinator import PresenceBridgeCoordinator

DATA_REGISTERED = "_registered"
CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)

START_PAIRING_SCHEMA = vol.Schema(
    {
        vol.Required("person"): cv.entity_id,
        vol.Optional("observer_id"): cv.string,
        vol.Optional("timeout_seconds", default=DEFAULT_PAIRING_TIMEOUT): vol.All(
            vol.Coerce(int), vol.Range(min=60, max=600)
        ),
    }
)
SET_OBSERVER_AREA_SCHEMA = vol.Schema(
    {
        vol.Required("observer_id"): cv.string,
        vol.Optional("area_id"): cv.string,
    }
)
REMOVE_IDENTITY_SCHEMA = vol.Schema({vol.Required("identity_id"): cv.string})


async def async_setup(hass: HomeAssistant, config: dict[str, Any]) -> bool:
    """Set up the integration namespace."""
    hass.data.setdefault(DOMAIN, {})
    return True


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up Presence Bridge from a config entry."""
    domain_data = hass.data.setdefault(DOMAIN, {})
    coordinator = PresenceBridgeCoordinator(hass, entry)
    await coordinator.async_setup()
    domain_data[entry.entry_id] = coordinator

    await _async_register_frontend(hass)
    if not domain_data.get(DATA_REGISTERED):
        _async_register_websocket_commands(hass)
        _async_register_services(hass)
        domain_data[DATA_REGISTERED] = True

    entry.async_on_unload(entry.add_update_listener(_async_reload_entry))
    await hass.config_entries.async_forward_entry_setups(
        entry,
        [Platform(platform) for platform in PLATFORMS],
    )
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a Presence Bridge entry."""
    unloaded = await hass.config_entries.async_unload_platforms(
        entry,
        [Platform(platform) for platform in PLATFORMS],
    )
    if not unloaded:
        return False
    coordinator: PresenceBridgeCoordinator = hass.data[DOMAIN].pop(entry.entry_id)
    await coordinator.async_unload()
    if not [key for key in hass.data[DOMAIN] if not key.startswith("_")]:
        frontend.async_remove_panel(hass, PANEL_URL)
    return True


async def _async_reload_entry(hass: HomeAssistant, entry: ConfigEntry) -> None:
    await hass.config_entries.async_reload(entry.entry_id)


def _coordinator(hass: HomeAssistant) -> PresenceBridgeCoordinator:
    for key, value in hass.data.get(DOMAIN, {}).items():
        if not key.startswith("_") and isinstance(value, PresenceBridgeCoordinator):
            return value
    raise RuntimeError("Presence Bridge is not configured")


@callback
def _async_register_services(hass: HomeAssistant) -> None:
    async def start_pairing(call: ServiceCall) -> dict[str, Any]:
        return await _coordinator(hass).async_start_pairing(
            call.data["person"],
            call.data.get("observer_id"),
            call.data["timeout_seconds"],
        )

    async def cancel_pairing(call: ServiceCall) -> dict[str, Any]:
        coordinator = _coordinator(hass)
        await coordinator.async_cancel_pairing(publish=True)
        return coordinator.pairing_payload()

    async def set_observer_area(call: ServiceCall) -> None:
        await _coordinator(hass).async_set_observer_area(
            call.data["observer_id"],
            call.data.get("area_id"),
        )

    async def remove_identity(call: ServiceCall) -> None:
        await _coordinator(hass).async_remove_identity(call.data["identity_id"])

    hass.services.async_register(
        DOMAIN,
        "start_pairing",
        start_pairing,
        schema=START_PAIRING_SCHEMA,
        supports_response=SupportsResponse.OPTIONAL,
    )
    hass.services.async_register(
        DOMAIN,
        "cancel_pairing",
        cancel_pairing,
        supports_response=SupportsResponse.OPTIONAL,
    )
    hass.services.async_register(
        DOMAIN,
        "set_observer_area",
        set_observer_area,
        schema=SET_OBSERVER_AREA_SCHEMA,
    )
    hass.services.async_register(
        DOMAIN,
        "remove_identity",
        remove_identity,
        schema=REMOVE_IDENTITY_SCHEMA,
    )


async def _async_register_frontend(hass: HomeAssistant) -> None:
    frontend_path = Path(__file__).parent / "frontend"
    await hass.http.async_register_static_paths(
        [StaticPathConfig(STATIC_URL, str(frontend_path), cache_headers=False)]
    )
    if PANEL_URL in hass.data.get("frontend_panels", {}):
        return
    frontend.async_register_built_in_panel(
        hass,
        component_name="custom",
        sidebar_title=PANEL_TITLE,
        sidebar_icon=PANEL_ICON,
        frontend_url_path=PANEL_URL,
        config={
            "_panel_custom": {
                "name": "presence-bridge-panel",
                "embed_iframe": False,
                "trust_external": False,
                "js_url": f"{STATIC_URL}/panel.js?v=2",
            }
        },
        require_admin=True,
    )


@websocket_api.websocket_command({vol.Required("type"): f"{DOMAIN}/info"})
@websocket_api.require_admin
@websocket_api.async_response
async def websocket_info(
    hass: HomeAssistant,
    connection: websocket_api.ActiveConnection,
    msg: dict[str, Any],
) -> None:
    connection.send_message(
        websocket_api.result_message(
            msg["id"],
            _coordinator(hass).public_payload(include_invitation=True),
        )
    )


@websocket_api.websocket_command(
    {
        vol.Required("type"): f"{DOMAIN}/start_pairing",
        vol.Required("person"): cv.entity_id,
        vol.Optional("observer_id"): cv.string,
        vol.Optional("timeout_seconds", default=DEFAULT_PAIRING_TIMEOUT): vol.All(
            vol.Coerce(int), vol.Range(min=60, max=600)
        ),
    }
)
@websocket_api.require_admin
@websocket_api.async_response
async def websocket_start_pairing(
    hass: HomeAssistant,
    connection: websocket_api.ActiveConnection,
    msg: dict[str, Any],
) -> None:
    try:
        result = await _coordinator(hass).async_start_pairing(
            msg["person"],
            msg.get("observer_id"),
            msg["timeout_seconds"],
        )
    except Exception as err:
        connection.send_error(msg["id"], "pairing_failed", str(err))
        return
    connection.send_message(websocket_api.result_message(msg["id"], result))


@websocket_api.websocket_command({vol.Required("type"): f"{DOMAIN}/cancel_pairing"})
@websocket_api.require_admin
@websocket_api.async_response
async def websocket_cancel_pairing(
    hass: HomeAssistant,
    connection: websocket_api.ActiveConnection,
    msg: dict[str, Any],
) -> None:
    coordinator = _coordinator(hass)
    await coordinator.async_cancel_pairing(publish=True)
    connection.send_message(
        websocket_api.result_message(msg["id"], coordinator.pairing_payload())
    )


@websocket_api.websocket_command(
    {
        vol.Required("type"): f"{DOMAIN}/set_observer_area",
        vol.Required("observer_id"): cv.string,
        vol.Optional("area_id"): cv.string,
    }
)
@websocket_api.require_admin
@websocket_api.async_response
async def websocket_set_observer_area(
    hass: HomeAssistant,
    connection: websocket_api.ActiveConnection,
    msg: dict[str, Any],
) -> None:
    try:
        await _coordinator(hass).async_set_observer_area(
            msg["observer_id"],
            msg.get("area_id"),
        )
    except Exception as err:
        connection.send_error(msg["id"], "area_update_failed", str(err))
        return
    connection.send_message(websocket_api.result_message(msg["id"], {"success": True}))


@websocket_api.websocket_command(
    {
        vol.Required("type"): f"{DOMAIN}/remove_identity",
        vol.Required("identity_id"): cv.string,
    }
)
@websocket_api.require_admin
@websocket_api.async_response
async def websocket_remove_identity(
    hass: HomeAssistant,
    connection: websocket_api.ActiveConnection,
    msg: dict[str, Any],
) -> None:
    try:
        await _coordinator(hass).async_remove_identity(msg["identity_id"])
    except Exception as err:
        connection.send_error(msg["id"], "remove_failed", str(err))
        return
    connection.send_message(websocket_api.result_message(msg["id"], {"success": True}))


@callback
def _async_register_websocket_commands(hass: HomeAssistant) -> None:
    websocket_api.async_register_command(hass, websocket_info)
    websocket_api.async_register_command(hass, websocket_start_pairing)
    websocket_api.async_register_command(hass, websocket_cancel_pairing)
    websocket_api.async_register_command(hass, websocket_set_observer_area)
    websocket_api.async_register_command(hass, websocket_remove_identity)
