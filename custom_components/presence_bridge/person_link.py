"""Idempotent, additive linking to HA's stored people without touching auth."""

from __future__ import annotations

from homeassistant.components import person
from homeassistant.exceptions import HomeAssistantError


async def async_link_tracker(hass, person_entity_id: str, tracker: str) -> None:
    state = hass.states.get(person_entity_id)
    if state is None:
        raise HomeAssistantError("person_not_ready")
    if tracker in state.attributes.get("device_trackers", []):
        return
    owners = person.persons_with_entity(hass, tracker)
    if any(owner != person_entity_id for owner in owners):
        raise HomeAssistantError("tracker_already_owned")
    if not state.attributes.get("editable"):
        raise HomeAssistantError("person_managed_in_yaml")
    # Person exposes creation/user linking, but no selected-person update helper.
    # Use its validated storage collection, never direct .storage file writes.
    data = hass.data.get("person")
    if not isinstance(data, list | tuple) or len(data) < 2:
        raise HomeAssistantError("person_api_unavailable")
    collection = data[1]
    person_id = state.attributes.get("id")
    current = next(
        (row for row in collection.async_items() if row["id"] == person_id), None
    )
    if current is None:
        raise HomeAssistantError("person_not_found")
    trackers = current.get("device_trackers", [])
    if tracker not in trackers:
        await collection.async_update_item(
            person_id, {"device_trackers": [*trackers, tracker]}
        )
