"""Shared entities for Presence Bridge."""

from __future__ import annotations

from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity import Entity

from .const import DOMAIN
from .coordinator import PresenceBridgeCoordinator


class PresenceBridgeIdentityEntity(Entity):
    """Base class for one privacy-preserving phone identity."""

    _attr_has_entity_name = True
    _attr_should_poll = False

    def __init__(
        self,
        coordinator: PresenceBridgeCoordinator,
        identity_id: str,
    ) -> None:
        self.coordinator = coordinator
        self.identity_id = identity_id
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, identity_id)},
            name=coordinator.identity_states[identity_id].label,
            manufacturer="Presence Bridge",
            model="Private BLE identity",
        )

    @property
    def available(self) -> bool:
        return self.identity_id in self.coordinator.identity_states

    @property
    def extra_state_attributes(self) -> dict[str, object]:
        if not self.available:
            return {}
        payload = self.coordinator.identity_payload(self.identity_id)
        return {
            key: value
            for key, value in payload.items()
            if key not in {"identity_id", "is_home", "label"}
        }
