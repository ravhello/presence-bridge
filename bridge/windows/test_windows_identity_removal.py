from __future__ import annotations

import ctypes
import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

from identity_removal import (
    BondDevice,
    _remove_remembered_device,
    identity_records,
    remove_identity_bonds,
    select_bond_devices,
    unpair_device,
)

KEY = "11" * 16
FINGERPRINT = hashlib.sha256(bytes.fromhex(KEY)).hexdigest()
RECORD = {"irk": KEY, "registry_leaf": "112233445566"}
BLE = BondDevice("ble-phone", "112233445566", "container-phone", "ble")
CLASSIC = BondDevice("classic-phone", "AABBCCDDEEFF", "container-phone", "classic")
OTHER = BondDevice("other", "223344556677", "other-container", "ble")


class IdentityRemovalTest(unittest.IsolatedAsyncioTestCase):
    def test_native_fallback_removes_only_exact_address(self):
        remove = Mock(return_value=0)
        with (
            patch("identity_removal.sys.platform", "win32"),
            patch(
                "identity_removal.ctypes.WinDLL",
                return_value=SimpleNamespace(BluetoothRemoveDevice=remove),
                create=True,
            ),
        ):
            _remove_remembered_device(CLASSIC.address)
        address = ctypes.cast(
            remove.call_args.args[0], ctypes.POINTER(ctypes.c_ulonglong)
        ).contents.value
        self.assertEqual(address, int(CLASSIC.address, 16))

    def test_native_failure_is_not_success(self):
        remove = Mock(return_value=5)
        with (
            patch("identity_removal.sys.platform", "win32"),
            patch(
                "identity_removal.ctypes.WinDLL",
                return_value=SimpleNamespace(BluetoothRemoveDevice=remove),
                create=True,
            ),
            self.assertRaises(OSError),
        ):
            _remove_remembered_device(CLASSIC.address)

    def test_native_rejects_invalid_address_before_loading_api(self):
        with (
            patch("identity_removal.ctypes.WinDLL", create=True) as loader,
            self.assertRaises(ValueError),
        ):
            _remove_remembered_device("iPhone")
        loader.assert_not_called()

    async def test_winrt_failed_uses_native_fallback_and_closes_peer(self):
        pairing = SimpleNamespace(
            unpair_async=AsyncMock(return_value=SimpleNamespace(status="failed"))
        )
        peer = SimpleNamespace(
            bluetooth_address=int(CLASSIC.address, 16),
            device_information=SimpleNamespace(pairing=pairing),
            close=Mock(),
        )
        bluetooth = SimpleNamespace(
            BluetoothDevice=SimpleNamespace(from_id_async=AsyncMock(return_value=peer)),
            BluetoothLEDevice=Mock(),
        )
        enumeration = SimpleNamespace(
            DeviceUnpairingResultStatus=SimpleNamespace(
                UNPAIRED="ok", ALREADY_UNPAIRED="absent"
            )
        )
        with (
            patch.dict(
                "sys.modules",
                {
                    "winrt.windows.devices.bluetooth": bluetooth,
                    "winrt.windows.devices.enumeration": enumeration,
                },
            ),
            patch("identity_removal._remove_remembered_device") as native,
        ):
            await unpair_device(CLASSIC)
        native.assert_called_once_with(CLASSIC.address)
        peer.close.assert_called_once()

    def test_only_matching_irk_and_shared_physical_container_are_selected(self):
        records = identity_records(
            [RECORD, {"irk": "22" * 16, "registry_leaf": OTHER.address}], FINGERPRINT
        )
        self.assertEqual(records, [RECORD])
        self.assertEqual(
            select_bond_devices(records, [BLE, CLASSIC, OTHER]), [BLE, CLASSIC]
        )

    def test_empty_container_never_matches_unrelated_devices(self):
        a = BondDevice("a", BLE.address, "", "ble")
        b = BondDevice("b", OTHER.address, "", "ble")
        self.assertEqual(select_bond_devices([RECORD], [a, b]), [a])

    async def test_success_requires_no_bonds_and_no_keys(self):
        with tempfile.TemporaryDirectory() as directory:
            journal = Path(directory) / "removals.json"
            with (
                patch(
                    "identity_removal.list_bonded_devices",
                    new=AsyncMock(side_effect=[[BLE, CLASSIC, OTHER], [OTHER]]),
                ),
                patch("identity_removal.unpair_device", new=AsyncMock()) as unpair,
            ):
                count = await remove_identity_bonds(
                    FINGERPRINT, Mock(side_effect=[[RECORD], []]), journal
                )
            self.assertEqual(count, 2)
            self.assertEqual(
                [call.args[0] for call in unpair.await_args_list], [BLE, CLASSIC]
            )
            self.assertEqual(json.loads(journal.read_text()), {})

    async def test_failed_partial_removal_can_retry_after_key_has_disappeared(self):
        with tempfile.TemporaryDirectory() as directory:
            journal = Path(directory) / "removals.json"
            with (
                patch(
                    "identity_removal.list_bonded_devices",
                    new=AsyncMock(return_value=[BLE, CLASSIC, OTHER]),
                ),
                patch(
                    "identity_removal.unpair_device",
                    new=AsyncMock(side_effect=[None, RuntimeError("Windows busy")]),
                ),
                self.assertRaisesRegex(RuntimeError, "Windows busy"),
            ):
                await remove_identity_bonds(FINGERPRINT, lambda: [RECORD], journal)
            self.assertIn(FINGERPRINT, json.loads(journal.read_text()))
            with (
                patch(
                    "identity_removal.list_bonded_devices",
                    new=AsyncMock(side_effect=[[CLASSIC, OTHER], [OTHER]]),
                ),
                patch("identity_removal.unpair_device", new=AsyncMock()) as unpair,
            ):
                await remove_identity_bonds(FINGERPRINT, list, journal)
            self.assertEqual(
                [call.args[0] for call in unpair.await_args_list], [BLE, CLASSIC]
            )

    async def test_key_still_present_is_not_success(self):
        with (
            tempfile.TemporaryDirectory() as directory,
            patch(
                "identity_removal.list_bonded_devices",
                new=AsyncMock(side_effect=[[BLE]] + [[]] * 8),
            ),
            patch("identity_removal.unpair_device", new=AsyncMock()),
            patch("identity_removal.asyncio.sleep", new=AsyncMock()),
            self.assertRaisesRegex(RuntimeError, "still reports"),
        ):
            await remove_identity_bonds(
                FINGERPRINT, lambda: [RECORD], Path(directory) / "removals.json"
            )

    async def test_unknown_mapping_does_not_remove_any_other_device(self):
        with (
            tempfile.TemporaryDirectory() as directory,
            patch(
                "identity_removal.list_bonded_devices",
                new=AsyncMock(return_value=[OTHER]),
            ),
            patch("identity_removal.unpair_device", new=AsyncMock()) as unpair,
            self.assertRaisesRegex(RuntimeError, "cannot identify"),
        ):
            await remove_identity_bonds(
                FINGERPRINT, lambda: [RECORD], Path(directory) / "removals.json"
            )
        unpair.assert_not_awaited()

    async def test_registry_access_failure_is_not_treated_as_already_removed(self):
        with (
            tempfile.TemporaryDirectory() as directory,
            patch("identity_removal.unpair_device", new=AsyncMock()) as unpair,
            self.assertRaises(PermissionError),
        ):
            await remove_identity_bonds(
                FINGERPRINT,
                Mock(side_effect=PermissionError()),
                Path(directory) / "removals.json",
            )
        unpair.assert_not_awaited()
