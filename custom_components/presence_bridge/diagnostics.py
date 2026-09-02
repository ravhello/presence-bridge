"""Diagnostics support for Presence Bridge."""

from __future__ import annotations

from homeassistant.core import HomeAssistant

from .const import DOMAIN


async def async_get_config_entry_diagnostics(hass: HomeAssistant, entry) -> dict:
    """Return diagnostics with all personal and cryptographic values redacted."""
    coordinator = hass.data[DOMAIN][entry.entry_id]
    return {
        "entry": {
            "title": entry.title,
            "options": dict(entry.options),
        },
        "runtime": coordinator.diagnostics_payload(),
    }
