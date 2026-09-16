"""Optional in-process Linux transport. MQTT is not required in this mode."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from types import SimpleNamespace

from .const import TOPIC_ROOT


class LocalReceiver:
    def __init__(self, coordinator):
        self.coordinator = coordinator
        self.receiver = None
        self.status = {
            "enabled": False,
            "ready": False,
            "message": "Local receiver disabled",
        }

    async def start(self):
        self.status["enabled"] = True
        if sys.platform != "linux":
            self.status["message"] = (
                "Local enrollment requires Linux/BlueZ; use a remote Windows or Linux receiver"
            )
            return
        try:
            # Optional imports: remote-only installations need no Linux packages.
            from .receiver.bluez import BlueZ
            from .receiver.engine import LinuxReceiver

            options = self.coordinator.entry.options
            self.receiver = LinuxReceiver(
                BlueZ(str(options.get("local_adapter", ""))),
                self.emit,
                Path(
                    self.coordinator.hass.config.path(
                        ".storage", "presence_bridge_linux", "peers.json"
                    )
                ),
                Path(str(options.get("bluez_storage", "/var/lib/bluetooth"))),
            )
            await self.receiver.start()
            self.status.update(
                ready=True, message="Local Linux receiver ready (experimental)"
            )
        except Exception as error:
            from .receiver.keys import ReceiverError

            self.status["message"] = (
                str(error)
                if isinstance(error, ReceiverError)
                else (
                    "Linux receiver unavailable: check BlueZ, D-Bus permissions and readable bond storage. "
                    f"Diagnostic category: {type(error).__name__}"
                )
            )
            self.receiver = None

    def emit(self, suffix, payload):
        if not self.receiver:
            return
        payload = {**payload, "observer_id": self.receiver.observer_id}
        message = SimpleNamespace(
            topic=f"{TOPIC_ROOT}/{self.receiver.observer_id}/{suffix}",
            payload=json.dumps(payload),
        )
        handlers = {
            "status": self.coordinator._status_message,
            "observations": self.coordinator._observations_message,
            "pairing/status": self.coordinator._pairing_status_message,
            "pairing/result": self.coordinator._pairing_result_message,
            "identity_removal/result": self.coordinator._identity_removal_result_message,
        }
        handlers[suffix](message)

    async def close(self):
        if self.receiver:
            await self.receiver.close()
            self.receiver = None
