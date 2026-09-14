import asyncio
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, Mock, patch

from fresh_pairing_test import reset_test_phone
from identity_removal import BondDevice


class FreshPairingTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.journal = Path(self.directory.name) / "targets.json"
        self.container = "11111111-1111-1111-1111-111111111111"
        self.classic = BondDevice(
            "classic-phone", "111122223333", self.container, "classic"
        )
        self.ble = BondDevice("ble-phone", "444455556666", self.container, "ble")
        self.other = BondDevice(
            "other", "AAAABBBBCCCC", "22222222-2222-2222-2222-222222222222", "ble"
        )
        self.phone_key = {"registry_leaf": self.ble.address, "irk": "11" * 16}
        self.other_key = {"registry_leaf": self.other.address, "irk": "22" * 16}

    async def test_removes_both_transports_and_preserves_other_device(self):
        remove = AsyncMock()
        result = await reset_test_phone(
            self.classic.address,
            self.container,
            self.journal,
            Mock(side_effect=[[self.phone_key, self.other_key], [self.other_key]]),
            remove,
            inventory=AsyncMock(
                side_effect=[[self.classic, self.ble, self.other], [self.other]]
            ),
        )
        self.assertTrue(result["clean"])
        self.assertEqual(result["removed_endpoints"], 2)
        remove.assert_awaited_once_with([self.classic, self.ble])
        self.assertNotIn(self.phone_key["irk"], self.journal.read_text())

    async def test_idempotent_when_phone_is_already_absent(self):
        remove = AsyncMock()
        result = await reset_test_phone(
            self.classic.address,
            self.container,
            self.journal,
            Mock(return_value=[self.other_key]),
            remove,
            inventory=AsyncMock(return_value=[self.other]),
        )
        self.assertTrue(result["clean"])
        self.assertEqual(result["removed_endpoints"], 0)
        remove.assert_not_awaited()

    async def test_residual_private_key_blocks_qr(self):
        with (
            patch("fresh_pairing_test.asyncio.sleep", new=AsyncMock()),
            self.assertRaisesRegex(RuntimeError, "still present"),
        ):
            await reset_test_phone(
                self.classic.address,
                self.container,
                self.journal,
                Mock(return_value=[self.phone_key]),
                AsyncMock(),
                inventory=AsyncMock(side_effect=[[self.ble]] + [[]] * 8),
            )

    async def test_partial_failure_retries_exact_journal_targets(self):
        with self.assertRaisesRegex(RuntimeError, "broker failed"):
            await reset_test_phone(
                self.classic.address,
                self.container,
                self.journal,
                Mock(return_value=[self.phone_key]),
                AsyncMock(side_effect=RuntimeError("broker failed")),
                inventory=AsyncMock(return_value=[self.classic, self.ble]),
            )
        remove = AsyncMock()
        result = await reset_test_phone(
            self.classic.address,
            self.container,
            self.journal,
            Mock(side_effect=[[self.phone_key], []]),
            remove,
            inventory=AsyncMock(side_effect=[[self.ble], []]),
        )
        self.assertTrue(result["clean"])
        remove.assert_awaited_once_with([self.ble])

    async def test_other_device_change_fails_closed(self):
        with self.assertRaisesRegex(RuntimeError, "unrelated"):
            await reset_test_phone(
                self.classic.address,
                self.container,
                self.journal,
                Mock(return_value=[]),
                AsyncMock(),
                inventory=AsyncMock(side_effect=[[self.classic, self.other], []]),
            )

    async def test_wrong_journal_phone_is_rejected_before_inventory(self):
        self.journal.write_text(json.dumps({"anchor": "FFFFFFFFFFFF"}))
        inventory = AsyncMock()
        with self.assertRaisesRegex(RuntimeError, "another phone"):
            await reset_test_phone(
                self.classic.address,
                self.container,
                self.journal,
                Mock(),
                AsyncMock(),
                inventory=inventory,
            )
        inventory.assert_not_awaited()

    async def test_cancellation_does_not_report_success(self):
        with self.assertRaises(asyncio.CancelledError):
            await reset_test_phone(
                self.classic.address,
                self.container,
                self.journal,
                Mock(return_value=[]),
                AsyncMock(side_effect=asyncio.CancelledError),
                inventory=AsyncMock(return_value=[self.classic]),
            )
