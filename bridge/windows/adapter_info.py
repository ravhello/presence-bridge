#!/usr/bin/env python3
"""Report the Windows Bluetooth roles needed by Presence Bridge."""

from __future__ import annotations

import asyncio
import json


async def adapter_info() -> dict[str, object]:
    """Return Bluetooth adapter capabilities exposed by WinRT."""
    from winrt.windows.devices.bluetooth import BluetoothAdapter  # noqa: PLC0415

    adapter = await BluetoothAdapter.get_default_async()
    if adapter is None:
        return {"adapter_found": False}
    return {
        "adapter_found": True,
        "bluetooth_address": f"{int(adapter.bluetooth_address):012X}",
        "is_central_role_supported": bool(adapter.is_central_role_supported),
        "is_low_energy_supported": bool(adapter.is_low_energy_supported),
        "is_peripheral_role_supported": bool(adapter.is_peripheral_role_supported),
    }


def main() -> int:
    print(json.dumps(asyncio.run(adapter_info()), sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
