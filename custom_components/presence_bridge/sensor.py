"""Room sensor entities for Presence Bridge."""

from __future__ import annotations

from homeassistant.components.sensor import SensorEntity
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.dispatcher import async_dispatcher_connect

from .const import DOMAIN, SIGNAL_IDENTITIES_UPDATED, SIGNAL_STATE_UPDATED
from .coordinator import PresenceBridgeCoordinator
from .entity import PresenceBridgeIdentityEntity


async def async_setup_entry(hass: HomeAssistant, entry, async_add_entities) -> None:
    coordinator: PresenceBridgeCoordinator = hass.data[DOMAIN][entry.entry_id]
    known: set[str] = set()

    @callback
    def add_entities() -> None:
        known.intersection_update(coordinator.identity_states)
        new_ids = set(coordinator.identity_states) - known
        if new_ids:
            known.update(new_ids)
            async_add_entities(
                PresenceBridgeRoomSensor(coordinator, identity_id)
                for identity_id in sorted(new_ids)
            )

    add_entities()
    entry.async_on_unload(
        async_dispatcher_connect(hass, SIGNAL_IDENTITIES_UPDATED, add_entities)
    )


class PresenceBridgeRoomSensor(PresenceBridgeIdentityEntity, SensorEntity):
    """Report the observer or area currently nearest to a phone."""

    _attr_name = "Bluetooth room"
    _attr_icon = "mdi:map-marker-account"

    def __init__(
        self, coordinator: PresenceBridgeCoordinator, identity_id: str
    ) -> None:
        super().__init__(coordinator, identity_id)
        self._attr_unique_id = f"{identity_id}_room"

    async def async_added_to_hass(self) -> None:
        self.async_on_remove(
            async_dispatcher_connect(
                self.hass,
                SIGNAL_STATE_UPDATED,
                self.async_write_ha_state,
            )
        )

    @property
    def native_value(self) -> str | None:
        state = self.coordinator.identity_states.get(self.identity_id)
        if state is None:
            return None
        payload = self.coordinator.identity_payload(self.identity_id)
        return str(
            payload.get("area_name")
            or state.observer_name
            or ("Away" if not state.is_home else "Home")
        )
