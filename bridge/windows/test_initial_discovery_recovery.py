"""Keep pre-claim Windows discovery retries bounded without deleting bonds."""

from __future__ import annotations

import asyncio
import time
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from protocol import PairingLink, pairing_service_uuid
from reverse_gatt_client import (
    ReverseGattPairingClient,
    ServiceDiscoveryBlockedError,
)


class InitialDiscoveryRecoveryTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.client = ReverseGattPairingClient(
            service_uuid="service",
            session_uuid="session",
            claim_uuid="claim",
            result_uuid="result",
        )
        self.device = SimpleNamespace(address="40:01:02:03:04:05")
        self.client._session_service_uuid = "current-qr-service"
        self.client._matched_service_uuid = "current-qr-service"

    async def run_blocked(self, saved_bond):
        with (
            patch.object(
                self.client,
                "_connect_candidate",
                new=AsyncMock(side_effect=TimeoutError),
            ) as connect,
            patch.object(
                self.client,
                "_candidate_has_saved_bond",
                new=AsyncMock(return_value=saved_bond),
            ) as inspect,
            patch.object(self.client, "_unpair_candidate", new=AsyncMock()) as unpair,
            patch("reverse_gatt_client.asyncio.sleep", new=AsyncMock()),
        ):
            for _ in range(2):
                with self.assertRaises(TimeoutError):
                    await self.client._open_candidate(self.device, 50)
                inspect.assert_not_awaited()
            with self.assertRaises(ServiceDiscoveryBlockedError) as raised:
                await self.client._open_candidate(self.device, 50)
            self.assertEqual(connect.await_count, 7)
            inspect.assert_awaited_once_with(self.device)
            unpair.assert_not_awaited()
            self.assertTrue(
                all(
                    not c.kwargs["pair_before_discovery"]
                    for c in connect.await_args_list
                )
            )
            return raised.exception

    async def test_saved_bond_gets_actionable_error_without_reset(self):
        failure = await self.run_blocked(True)
        self.assertEqual(failure.detail_code, "iphone_saved_bond_unreachable")
        self.assertIn("If the iPhone forgot", str(failure))

    async def test_no_bond_is_not_misdiagnosed_as_stale_pairing(self):
        failure = await self.run_blocked(False)
        self.assertEqual(failure.detail_code, "iphone_service_unreachable")

    async def test_unknown_bond_state_does_not_claim_corruption(self):
        failure = await self.run_blocked(None)
        self.assertEqual(failure.detail_code, "iphone_service_unreachable")

    async def test_success_clears_counter_only_for_the_same_phone(self):
        self.client._initial_discovery_failures = {
            self.device.address.casefold(): 2,
            "other": 2,
        }
        peer = SimpleNamespace()
        with (
            patch.object(
                self.client, "_connect_candidate", new=AsyncMock(return_value=peer)
            ),
            patch.object(self.client, "_gatt_inventory", return_value="session"),
        ):
            self.assertIs(await self.client._open_candidate(self.device, 30), peer)
        self.assertEqual(self.client._initial_discovery_failures, {"other": 2})

    async def test_cancellation_does_not_become_recovery(self):
        with (
            patch.object(
                self.client,
                "_connect_candidate",
                new=AsyncMock(side_effect=asyncio.CancelledError),
            ),
            patch.object(
                self.client, "_candidate_has_saved_bond", new=AsyncMock()
            ) as inspect,
        ):
            with self.assertRaises(asyncio.CancelledError):
                await self.client._open_candidate(self.device, 30)
            inspect.assert_not_awaited()
        self.assertEqual(self.client._initial_discovery_failures, {})

    async def test_confirmed_bond_keeps_existing_post_bond_policy(self):
        self.client._secure_bond_confirmed = True
        with (
            patch.object(
                self.client,
                "_connect_candidate",
                new=AsyncMock(side_effect=TimeoutError),
            ),
            patch.object(
                self.client, "_candidate_has_saved_bond", new=AsyncMock()
            ) as inspect,
            patch("reverse_gatt_client.asyncio.sleep", new=AsyncMock()),
        ):
            for _ in range(3):
                with self.assertRaises(TimeoutError):
                    await self.client._open_candidate(self.device, 30)
            inspect.assert_not_awaited()
        self.assertEqual(self.client._initial_discovery_failures, {})

    async def test_terminal_discovery_failure_does_not_restart_outer_loop(self):
        link = PairingLink(
            "abcdefghijklmnopQRSTUVWX",
            "receiver",
            int(time.time()) + 180,
            bytes(range(32)),
        )
        advertisement = SimpleNamespace(
            service_uuids=[pairing_service_uuid(link)], service_data={}, rssi=-55
        )
        failure = ServiceDiscoveryBlockedError(
            "Unavailable", "iphone_saved_bond_unreachable"
        )

        async def find(filter_func, **_kwargs):
            self.assertTrue(filter_func(self.device, advertisement))
            return self.device

        with (
            patch(
                "reverse_gatt_client.BleakScanner.find_device_by_filter",
                new=AsyncMock(side_effect=find),
            ),
            patch.object(
                self.client, "_pair_candidate", new=AsyncMock(side_effect=failure)
            ) as pair,
        ):
            with self.assertRaises(ServiceDiscoveryBlockedError):
                await self.client.async_pair(link, 180)
            pair.assert_awaited_once()


if __name__ == "__main__":
    unittest.main()
