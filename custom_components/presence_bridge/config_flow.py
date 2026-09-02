"""Config flow for Presence Bridge."""

from __future__ import annotations

from typing import Any

import voluptuous as vol
from homeassistant import config_entries
from homeassistant.const import CONF_NAME

from .const import (
    CONF_AWAY_TIMEOUT,
    CONF_OBSERVER_TIMEOUT,
    DEFAULT_AWAY_TIMEOUT,
    DEFAULT_OBSERVER_TIMEOUT,
    DOMAIN,
    PANEL_TITLE,
)


class PresenceBridgeConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Configure a local Presence Bridge installation."""

    VERSION = 1

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.ConfigFlowResult:
        """Create the single integration entry."""
        if self._async_current_entries():
            return self.async_abort(reason="single_instance_allowed")

        errors: dict[str, str] = {}
        if not self.hass.config_entries.async_entries("mqtt"):
            errors["base"] = "mqtt_not_configured"
        elif user_input is not None:
            return self.async_create_entry(
                title=str(user_input.get(CONF_NAME) or PANEL_TITLE),
                data={},
            )

        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema({vol.Optional(CONF_NAME, default=PANEL_TITLE): str}),
            errors=errors,
        )

    @staticmethod
    def async_get_options_flow(config_entry) -> PresenceBridgeOptionsFlow:
        """Return the options flow."""
        return PresenceBridgeOptionsFlow(config_entry)


class PresenceBridgeOptionsFlow(config_entries.OptionsFlow):
    """Configure presence expiry without restarting the bridge."""

    def __init__(self, config_entry) -> None:
        self.config_entry = config_entry

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.ConfigFlowResult:
        if user_input is not None:
            return self.async_create_entry(title="", data=user_input)
        return self.async_show_form(
            step_id="init",
            data_schema=vol.Schema(
                {
                    vol.Optional(
                        CONF_AWAY_TIMEOUT,
                        default=self.config_entry.options.get(
                            CONF_AWAY_TIMEOUT, DEFAULT_AWAY_TIMEOUT
                        ),
                    ): vol.All(vol.Coerce(int), vol.Range(min=30, max=1800)),
                    vol.Optional(
                        CONF_OBSERVER_TIMEOUT,
                        default=self.config_entry.options.get(
                            CONF_OBSERVER_TIMEOUT, DEFAULT_OBSERVER_TIMEOUT
                        ),
                    ): vol.All(vol.Coerce(int), vol.Range(min=30, max=300)),
                }
            ),
        )
