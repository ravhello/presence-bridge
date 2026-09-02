"""Runtime models for Presence Bridge."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any


@dataclass(slots=True)
class ObserverState:
    """Last state received from one bridge observer."""

    observer_id: str
    name: str
    online: bool = False
    area_id: str | None = None
    last_seen: datetime | None = None
    observations: list[dict[str, Any]] = field(default_factory=list)
    capabilities: list[str] = field(default_factory=list)
    version: str = ""


@dataclass(slots=True)
class IdentityState:
    """Resolved private BLE identity state."""

    identity_id: str
    person_entity_id: str
    label: str
    is_home: bool = False
    observer_id: str | None = None
    observer_name: str | None = None
    area_id: str | None = None
    rssi: int | None = None
    last_seen: datetime | None = None
