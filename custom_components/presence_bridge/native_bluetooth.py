"""Read HA's shared Bluetooth cache without opening connections or scanners."""

from __future__ import annotations

import time
from datetime import UTC, datetime, timedelta

from homeassistant.components import bluetooth
from homeassistant.helpers import device_registry as dr


def refresh_native_observations(coordinator) -> None:
    """Use actual advertisement times; polling never makes old packets fresh."""
    if "bluetooth" not in coordinator.hass.config.components:
        return
    try:
        scanners = bluetooth.async_current_scanners(coordinator.hass)
        infos = bluetooth.async_discovered_service_info(coordinator.hass, False)
    except (KeyError, RuntimeError):
        return
    now = datetime.now(UTC)
    monotonic_now = time.monotonic()
    active = {}
    registry = dr.async_get(coordinator.hass)
    settings = coordinator.memory.get("observer_settings", {})
    for scanner in scanners:
        source = str(scanner.source)
        observer_id = "ha_" + source.lower().replace(":", "_")
        observer = coordinator._ensure_observer(observer_id, {"name": source})
        device = registry.async_get_device(
            connections={(dr.CONNECTION_BLUETOOTH, source)}
        )
        observer.name = (
            ((device.name_by_user or device.name) if device else None)
            or getattr(scanner, "name", None)
            or f"HA Bluetooth {source}"
        )
        if observer_id not in settings:
            observer.area_id = device.area_id if device else None
        observer.capabilities = ["scanner", "ha_bluetooth"]
        observer.online = bool(getattr(scanner, "scanning", True))
        if observer.online:
            observer.last_seen = now
        else:
            observer.last_seen = None
        observer.observations = []
        active[source] = observer
    for observer in coordinator.observers.values():
        if "ha_bluetooth" in observer.capabilities and observer not in active.values():
            observer.online = False
            observer.last_seen = None
            observer.observations = []
    for info in infos:
        observer = active.get(str(info.source))
        if observer is None or not observer.online:
            continue
        age = monotonic_now - info.time
        if not -5 <= age <= coordinator.away_timeout:
            continue
        if len(observer.observations) >= 200:
            continue
        observer.observations.extend(
            coordinator._normalize_observations(
                [
                    {
                        "address": info.address,
                        "rssi": info.rssi,
                        "seen_at": (now - timedelta(seconds=max(0, age))).isoformat(),
                    }
                ]
            )
        )
