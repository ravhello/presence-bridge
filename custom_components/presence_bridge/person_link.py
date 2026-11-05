"""Read-only Person assignment status from Home Assistant's public states."""

from __future__ import annotations


def person_link_status(hass, person_entity_id: str, tracker: str | None) -> str:
    """Never change Person data; the owner manages trackers in Settings > People."""
    selected = hass.states.get(person_entity_id)
    if selected is None:
        return "person_not_ready"
    if not tracker:
        return "tracker_pending"
    owners = [
        state.entity_id
        for state in hass.states.async_all("person")
        if tracker in state.attributes.get("device_trackers", [])
    ]
    if any(owner != person_entity_id for owner in owners):
        return "tracker_already_owned"
    if person_entity_id in owners:
        return "linked"
    if not selected.attributes.get("editable", True):
        return "person_managed_in_yaml"
    return "manual_configuration_required"
