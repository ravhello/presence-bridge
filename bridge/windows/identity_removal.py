"""Remove one verified phone's Windows bonds, never devices selected by name."""

from __future__ import annotations

import asyncio
import ctypes
import hashlib
import json
import re
import sys
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any
from uuid import UUID


@dataclass(frozen=True)
class BondDevice:
    device_id: str
    address: str
    container_id: str
    transport: str


def compact_address(value: str) -> str:
    value = value.replace(":", "").replace("-", "").upper()
    return value if re.fullmatch(r"[0-9A-F]{12}", value) else ""


def identity_records(
    records: list[dict[str, str]], fingerprint: str
) -> list[dict[str, str]]:
    if not re.fullmatch(r"[0-9a-f]{64}", fingerprint):
        raise ValueError("Invalid private identity fingerprint")
    return [
        row
        for row in records
        if re.fullmatch(r"[0-9A-Fa-f]{32}", row.get("irk", ""))
        and hashlib.sha256(bytes.fromhex(row["irk"])).hexdigest() == fingerprint
    ]


def select_bond_devices(
    records: list[dict[str, str]], devices: list[BondDevice]
) -> list[BondDevice]:
    addresses = {compact_address(row.get("registry_leaf", "")) for row in records} - {
        ""
    }
    direct = [device for device in devices if device.address in addresses]
    containers = {device.container_id for device in direct if device.container_id}
    return [
        device
        for device in devices
        if device.address in addresses or device.container_id in containers
    ]


async def list_bonded_devices() -> list[BondDevice]:
    if sys.platform != "win32":
        raise RuntimeError("Windows Bluetooth removal is available only on Windows")
    from winrt.system import unbox_guid, unbox_string
    from winrt.windows.devices.bluetooth import BluetoothDevice, BluetoothLEDevice
    from winrt.windows.devices.enumeration import DeviceInformation

    result = []
    for transport, kind in (("classic", BluetoothDevice), ("ble", BluetoothLEDevice)):
        devices = (
            await DeviceInformation.find_all_async_aqs_filter_and_additional_properties(
                kind.get_device_selector_from_pairing_state(True),
                ["System.Devices.Aep.ContainerId"],
            )
        )
        for item in devices:
            peer = await kind.from_id_async(item.id)
            if peer is None:
                raise RuntimeError("Windows could not inspect a saved Bluetooth device")
            try:
                # Interface-level IsPaired can be false. The Bluetooth peer's AEP
                # provides the actual bond state, without connecting to GATT.
                if not peer.device_information.pairing.is_paired:
                    continue
                container = ""
                raw = item.properties.get("System.Devices.Aep.ContainerId")
                for unpack in (unbox_guid, unbox_string):
                    try:
                        parsed = UUID(str(unpack(raw)))
                        if parsed.int:
                            container = str(parsed)
                        break
                    except (TypeError, ValueError, OSError):
                        continue
                result.append(
                    BondDevice(
                        item.id, f"{peer.bluetooth_address:012X}", container, transport
                    )
                )
            finally:
                peer.close()
    return result


async def unpair_device(device: BondDevice) -> None:
    from winrt.windows.devices.bluetooth import BluetoothDevice, BluetoothLEDevice
    from winrt.windows.devices.enumeration import DeviceUnpairingResultStatus

    if device.transport not in {"classic", "ble"} or not compact_address(
        device.address
    ):
        raise ValueError("Invalid Windows Bluetooth removal target")
    kind = BluetoothDevice if device.transport == "classic" else BluetoothLEDevice
    peer = await kind.from_id_async(device.device_id)
    if peer is None:
        return  # The final inventory still has to prove absence.
    try:
        if f"{peer.bluetooth_address:012X}" != device.address:
            raise RuntimeError("Windows device address changed; removal stopped")
        outcome = await peer.device_information.pairing.unpair_async()
        if outcome.status not in {
            DeviceUnpairingResultStatus.UNPAIRED,
            DeviceUnpairingResultStatus.ALREADY_UNPAIRED,
        }:
            # The WinRT user-session broker can return FAILED to a SYSTEM service.
            # The desktop API removes only this exact remembered Bluetooth address.
            await asyncio.to_thread(_remove_remembered_device, device.address)
    finally:
        peer.close()


def _remove_remembered_device(address: str) -> None:
    if sys.platform != "win32" or not re.fullmatch(r"[0-9A-F]{12}", address):
        raise ValueError("Invalid native Bluetooth removal target")
    library = ctypes.WinDLL("bthprops.cpl", use_last_error=True)
    remove = library.BluetoothRemoveDevice
    remove.argtypes = [ctypes.POINTER(ctypes.c_ulonglong)]
    remove.restype = ctypes.c_ulong
    target = ctypes.c_ulonglong(int(address, 16))
    result = remove(ctypes.byref(target))
    if result not in {
        0,
        1168,
    }:  # ERROR_SUCCESS / ERROR_NOT_FOUND; verify inventory next.
        raise OSError(
            result, "Windows could not remove the remembered Bluetooth device"
        )


def _save_journal(path: Path, journal: dict[str, Any]) -> None:
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(journal), encoding="utf-8")
    temporary.replace(path)


async def remove_identity_bonds(
    fingerprint: str, read_records: Any, journal_path: Path, unpair_targets: Any = None
) -> int:
    """Keep recovery targets until both OS pairing and private keys are absent."""
    async with asyncio.timeout(55):
        records = identity_records(await asyncio.to_thread(read_records), fingerprint)
        devices = await list_bonded_devices()
        journal = (
            json.loads(journal_path.read_text(encoding="utf-8"))
            if journal_path.exists()
            else {}
        )
        if not isinstance(journal, dict):
            raise RuntimeError("Invalid Bluetooth removal recovery journal")
        targets = select_bond_devices(records, devices)
        previous = [BondDevice(**row) for row in journal.get(fingerprint, [])]
        targets = list(
            {device.device_id: device for device in [*previous, *targets]}.values()
        )
        if records and not targets:
            raise RuntimeError(
                "The private identity exists but Windows cannot identify its paired device"
            )
        if targets:
            journal[fingerprint] = [asdict(device) for device in targets]
            _save_journal(journal_path, journal)
        if unpair_targets is not None and targets:
            await unpair_targets(targets)
        else:
            for device in targets:
                await unpair_device(device)
        for _attempt in range(8):
            remaining = await list_bonded_devices()
            target_ids = {device.device_id for device in targets}
            target_addresses = {device.address for device in targets}
            still_paired = any(
                device.device_id in target_ids or device.address in target_addresses
                for device in remaining
            )
            keys = identity_records(await asyncio.to_thread(read_records), fingerprint)
            if not still_paired and not keys:
                journal.pop(fingerprint, None)
                _save_journal(journal_path, journal)
                return len(targets)
            await asyncio.sleep(0.5)
        raise RuntimeError(
            "Windows still reports a saved bond or private key; retry removal"
        )
