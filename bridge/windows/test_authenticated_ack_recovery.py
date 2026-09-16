"""Regression for WinRT dropping GATT after a successful authenticated bond."""

from __future__ import annotations

import asyncio
import json
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

from bleak import BleakScanner
from bleak.exc import BleakGATTProtocolError
from numeric_pairing_probe import QRSessionPairing
from protocol import PairingLink, claim_proof
from reverse_gatt_client import (
    PairingDiagnosticError,
    ReverseGattError,
    ReverseGattPairingClient,
    ReverseGattTimeoutError,
)


class AuthenticatedAckRecoveryTest(unittest.IsolatedAsyncioTestCase):
    async def run_case(self, scenario):
        link = PairingLink(
            session_id="abcdefghijklmnopQRSTUVWX",
            observer_id="receiver",
            expires_at=int(time.time()) + 180,
            secret=bytes(range(32)),
        )
        session = {
            "v": link.version,
            "sid": link.session_id,
            "oid": link.observer_id,
            "exp": link.expires_at,
        }
        device = SimpleNamespace(address="AA:BB:CC:DD:EE:FF", name="iPhone")
        transports = []
        leases = []
        read_events = []
        events = []
        with tempfile.TemporaryDirectory() as directory:
            policy = QRSessionPairing(Path(directory), Mock(), Mock())
            client = ReverseGattPairingClient(
                service_uuid="service",
                session_uuid="session",
                claim_uuid="claim",
                result_uuid="result",
                pairing_probe=policy,
                progress_callback=lambda code, _message: events.append(code),
            )

            async def open_peer(*_args):
                number = len(transports)
                native = SimpleNamespace(
                    is_connected=True,
                    pair=AsyncMock(),
                    unpair=AsyncMock(),
                    disconnect=AsyncMock(),
                )

                async def read(uuid):
                    read_events.append((number, uuid))
                    payload = dict(session)
                    if uuid == "claim":
                        payload["proof"] = claim_proof(link)
                        if scenario == "changed_claim" and number > 0:
                            payload["proof"] = "invalid"
                    return json.dumps(payload).encode()

                async def write(*_args, **_kwargs):
                    leases.append(client._completion_deadline)
                    if number > 0 and scenario != "persistent":
                        return
                    if scenario != "still_connected":
                        native.is_connected = False
                    if scenario == "cancelled":
                        raise asyncio.CancelledError()
                    if scenario == "att_rejected":
                        raise BleakGATTProtocolError(0x05)
                    if scenario == "deadline":
                        client._completion_deadline = time.monotonic() - 1
                    error = OSError("Windows cancelled the closed GATT operation")
                    error.winerror = -2147023673
                    raise error

                native.read_gatt_char = AsyncMock(side_effect=read)
                native.write_gatt_char = AsyncMock(side_effect=write)
                transports.append(native)
                return native

            native_pair = AsyncMock(side_effect=[False, True, True])
            if scenario == "rejected":
                native_pair.side_effect = PairingDiagnosticError(
                    "User declined", "numeric_pairing_not_authenticated"
                )
            expected = {
                "success": None,
                "persistent": ReverseGattError,
                "cancelled": asyncio.CancelledError,
                "att_rejected": PairingDiagnosticError,
                "still_connected": PairingDiagnosticError,
                "changed_claim": ReverseGattError,
                "deadline": ReverseGattTimeoutError,
                "rejected": PairingDiagnosticError,
            }[scenario]
            with (
                patch.object(
                    BleakScanner,
                    "find_device_by_filter",
                    new=AsyncMock(return_value=device),
                ),
                patch.object(
                    client, "_open_candidate", new=AsyncMock(side_effect=open_peer)
                ),
                patch.object(client, "_require_ack_encryption") as protect,
                patch.object(client, "_release_client", new=AsyncMock()) as release,
                patch(
                    "numeric_pairing_probe.pair_with_numeric_comparison", native_pair
                ),
                patch("reverse_gatt_client.asyncio.sleep", new=AsyncMock()),
            ):
                if expected:
                    with self.assertRaises(expected) as raised:
                        await client.async_pair(link, 30)
                    if scenario == "persistent":
                        self.assertEqual(
                            raised.exception.detail_code, "iphone_bond_link_failed"
                        )
                else:
                    result = await client.async_pair(link, 30)
                    self.assertTrue(result.secure_exchange_complete)
                    self.assertEqual(result.address, device.address)
            for native in transports:
                native.pair.assert_not_awaited()
                native.unpair.assert_not_awaited()
            self.assertEqual(release.await_count, len(transports))
            for call in protect.call_args_list:
                self.assertTrue(call.kwargs["require_authentication"])
            if scenario == "success":
                self.assertEqual(len(transports), 2)
                self.assertEqual(
                    read_events,
                    [(0, "session"), (0, "claim"), (1, "session"), (1, "claim")],
                )
                self.assertEqual(native_pair.await_count, 2)
                self.assertEqual(leases[0], leases[1])
                self.assertIn(
                    device.address.casefold(), client._fresh_discovery_addresses
                )
                self.assertEqual(events[-1], "iphone_claim_accepted")
            elif scenario == "persistent":
                self.assertEqual(len(transports), 3)
                self.assertEqual(len(set(leases)), 1)
            elif scenario == "changed_claim":
                self.assertEqual(native_pair.await_count, 1)
                for native in transports[1:]:
                    native.write_gatt_char.assert_not_awaited()
            else:
                self.assertEqual(len(transports), 1)
            if scenario != "success":
                self.assertNotIn("iphone_claim_accepted", events)

    async def test_closed_authenticated_channel_recovers_with_fresh_proofs(self):
        await self.run_case("success")

    async def test_persistent_disconnect_is_bounded_without_bond_reset(self):
        await self.run_case("persistent")

    async def test_recovery_never_overrides_cancellation_or_security_rejection(self):
        for scenario in ("cancelled", "att_rejected", "still_connected", "rejected"):
            with self.subTest(scenario=scenario):
                await self.run_case(scenario)

    async def test_reconnect_revalidates_claim(self):
        await self.run_case("changed_claim")

    async def test_reconnect_does_not_extend_the_attempt_deadline(self):
        await self.run_case("deadline")
