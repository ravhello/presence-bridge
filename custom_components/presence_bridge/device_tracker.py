"""Device tracker entities for Presence Bridge."""

from __future__ import annotations

from homeassistant.components.device_tracker import BaseScannerEntity, SourceType
from homeassistant.const import EVENT_HOMEASSISTANT_STARTED
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.dispatcher import async_dispatcher_connect

from .const import DOMAIN, SIGNAL_IDENTITIES_UPDATED, SIGNAL_STATE_UPDATED
from .coordinator import PresenceBridgeCoordinator
from .entity import PresenceBridgeIdentityEntity
from .person_link import async_link_tracker


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
                PresenceBridgeTracker(coordinator, identity_id)
                for identity_id in sorted(new_ids)
            )

    add_entities()
    entry.async_on_unload(
        async_dispatcher_connect(hass, SIGNAL_IDENTITIES_UPDATED, add_entities)
    )


class PresenceBridgeTracker(PresenceBridgeIdentityEntity, BaseScannerEntity):
    """Expose a paired phone as a local Bluetooth tracker."""

    _attr_name = "Bluetooth tracker"
    _attr_source_type = SourceType.BLUETOOTH

    def __init__(
        self, coordinator: PresenceBridgeCoordinator, identity_id: str
    ) -> None:
        super().__init__(coordinator, identity_id)
        self._attr_unique_id = f"{identity_id}_tracker"

    async def async_added_to_hass(self) -> None:
        self.async_on_remove(
            async_dispatcher_connect(
                self.hass,
                SIGNAL_STATE_UPDATED,
                self.async_write_ha_state,
            )
        )
        if self.hass.is_running:
            await self._async_link_person()
        else:
            self.async_on_remove(
                self.hass.bus.async_listen_once(
                    EVENT_HOMEASSISTANT_STARTED, self._async_link_person
                )
            )

    async def _async_link_person(self, _event=None) -> None:
        row = self.coordinator.memory.get("identities", {}).get(self.identity_id)
        if not row or row.get("person_link_completed"):
            return
        try:
            await async_link_tracker(self.hass, row["person_entity_id"], self.entity_id)
        except HomeAssistantError as error:
            row["person_link_status"] = str(error)
        else:
            row["person_link_completed"] = True
            row["person_link_status"] = "linked"
        await self.coordinator.store.async_save(self.coordinator.memory)

    @property
    def state(self) -> str | None:
        # Missing radio coverage must not override GPS/Wi-Fi as proven absence.
        return "home" if self.is_connected else None

    @property
    def is_connected(self) -> bool:
        state = self.coordinator.identity_states.get(self.identity_id)
        return bool(state and state.is_home)
