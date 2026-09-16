"""Product-path matrix for saved, partial and mismatched Bluetooth bonds."""

from __future__ import annotations

import asyncio
import json
import time
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from bleak.exc import BleakGATTProtocolError
from identity_removal import BondDevice
from protocol import PairingLink, claim_proof
from reverse_gatt_client import (
    BondResetRequiredError,
    PairingDiagnosticError,
    ReverseGattError,
    ReverseGattPairingClient,
    ReverseGattTimeoutError,
)


class PartialPairingRecoveryTest(unittest.IsolatedAsyncioTestCase):
    def new_client(self, policy=None):
        return ReverseGattPairingClient(
            service_uuid="service",
            session_uuid="session",
            claim_uuid="claim",
            result_uuid="result",
            pairing_probe=policy,
        )

    async def scenario(self, mode):
        link = PairingLink(
            "abcdefghijklmnopQRSTUVWX",
            "receiver",
            int(time.time()) + 180,
            bytes(range(32)),
        )
        session = {
            "v": link.version,
            "sid": link.session_id,
            "oid": link.observer_id,
            "exp": link.expires_at,
        }
        device = SimpleNamespace(address="40:01:02:03:04:05", name="iPhone")
        remove = AsyncMock()
        leases = []
        connections = []

        class Policy:
            reused_bond = False
            allow_link_recovery = True
            allow_bond_repair = True
            calls = 0

            async def __call__(self, *_args):
                self.calls += 1
                self.reused_bond = (
                    mode not in {"fresh", "phone_only", "phone_rejects"}
                    and not remove.await_count
                )
                if mode == "weak" and not remove.await_count:
                    raise PairingDiagnosticError(
                        "Saved weak bond", "numeric_pairing_saved_bond_weak"
                    )
                if mode == "phone_rejects":
                    raise PairingDiagnosticError(
                        "iOS rejected pairing", "numeric_pairing_not_authenticated"
                    )
                return True

        policy = Policy()
        client = self.new_client(policy)

        async def open_peer(*_args):
            native = SimpleNamespace(
                is_connected=True, pair=AsyncMock(), unpair=AsyncMock()
            )

            async def read(uuid):
                payload = dict(session)
                if uuid == "claim":
                    payload["proof"] = (
                        "wrong" if mode == "wrong_claim" else claim_proof(link)
                    )
                return json.dumps(payload).encode()

            async def write(*_args, **_kwargs):
                leases.append(client._completion_deadline)
                if not remove.await_count and mode == "receiver_only":
                    raise BleakGATTProtocolError(0x05)
                if mode == "both_broken" and not remove.await_count:
                    native.is_connected = False
                    raise OSError("Link closed")
                if mode == "persistent" and not remove.await_count:
                    raise BleakGATTProtocolError(0x0F)
                if mode == "persistent" and remove.await_count:
                    raise BleakGATTProtocolError(0x05)

            native.read_gatt_char = AsyncMock(side_effect=read)
            native.write_gatt_char = AsyncMock(side_effect=write)
            connections.append(native)
            return native

        with (
            patch(
                "reverse_gatt_client.BleakScanner.find_device_by_filter",
                new=AsyncMock(return_value=device),
            ),
            patch.object(
                client, "_open_candidate", new=AsyncMock(side_effect=open_peer)
            ),
            patch.object(client, "_remove_verified_peer_bonds", remove),
            patch.object(client, "_require_ack_encryption"),
            patch.object(client, "_release_client", new=AsyncMock()),
            patch("reverse_gatt_client.asyncio.sleep", new=AsyncMock()),
        ):
            if mode in {"phone_rejects", "persistent", "wrong_claim"}:
                # A rejected claim remains a bounded session failure, not repair.
                if mode == "wrong_claim":
                    with self.assertRaises(ReverseGattError):
                        await client._pair_candidate(
                            device, link, 30, allow_bond_reset=True
                        )
                else:
                    with self.assertRaises(PairingDiagnosticError):
                        await client.async_pair(link, 30)
            else:
                result = await client.async_pair(link, 30)
                self.assertTrue(result.secure_exchange_complete)
            expected_repairs = int(
                mode in {"receiver_only", "both_broken", "weak", "persistent"}
            )
            self.assertEqual(remove.await_count, expected_repairs)
            if leases:
                self.assertEqual(
                    len(set(leases)), 1, "Recovery must not extend the deadline"
                )
            if mode in {"fresh", "phone_only", "both_valid"}:
                self.assertEqual(len(connections), 1)
            if mode == "both_broken":
                self.assertEqual(len(connections), 4)
            if mode == "wrong_claim":
                self.assertEqual(policy.calls, 0)

    async def test_neither_side_paired(self):
        await self.scenario("fresh")

    async def test_phone_remembers_receiver_but_server_is_new(self):
        await self.scenario("phone_only")

    async def test_both_remember_valid_pairing_without_new_prompt(self):
        await self.scenario("both_valid")

    async def test_receiver_only_repairs_after_explicit_encryption_rejection(self):
        await self.scenario("receiver_only")

    async def test_both_remember_broken_bond_repairs_after_reconnect_attempts(self):
        await self.scenario("both_broken")

    async def test_weak_saved_bond_is_repaired_after_claim_verification(self):
        await self.scenario("weak")

    async def test_persistent_failure_does_not_start_pairing_loop(self):
        await self.scenario("persistent")

    async def test_ios_refusal_does_not_erase_unrelated_state_or_claim_success(self):
        await self.scenario("phone_rejects")

    async def test_invalid_claim_cannot_authorize_repair(self):
        await self.scenario("wrong_claim")

    async def test_repair_budget_is_global_even_if_phone_address_changes(self):
        client = self.new_client()
        one = SimpleNamespace(address="40:01:02:03:04:05")
        two = SimpleNamespace(address="40:01:02:03:04:06")
        client._qr_verified_addresses = {one.address.casefold(), two.address.casefold()}
        with patch.object(
            client, "_remove_verified_peer_bonds", new=AsyncMock()
        ) as remove:
            with self.assertRaises(BondResetRequiredError):
                await client._repair_verified_bond(one, None, time.monotonic() + 60)
            with self.assertRaises(PairingDiagnosticError):
                await client._repair_verified_bond(two, None, time.monotonic() + 60)
            remove.assert_awaited_once()

    async def test_deadline_or_unverified_peer_cannot_trigger_removal(self):
        client = self.new_client()
        device = SimpleNamespace(address="40:01:02:03:04:05")
        with patch.object(
            client, "_remove_verified_peer_bonds", new=AsyncMock()
        ) as remove:
            with self.assertRaises(PairingDiagnosticError):
                await client._repair_verified_bond(device, None, time.monotonic() + 60)
            client._qr_verified_addresses.add(device.address.casefold())
            with self.assertRaises(ReverseGattTimeoutError):
                await client._repair_verified_bond(device, None, time.monotonic() + 1)
            remove.assert_not_awaited()

    async def test_cancellation_during_cleanup_never_retries_removal(self):
        client = self.new_client()
        device = SimpleNamespace(address="40:01:02:03:04:05")
        client._qr_verified_addresses.add(device.address.casefold())
        with patch.object(
            client,
            "_remove_verified_peer_bonds",
            new=AsyncMock(side_effect=asyncio.CancelledError),
        ) as remove:
            with self.assertRaises(asyncio.CancelledError):
                await client._repair_verified_bond(device, None, time.monotonic() + 60)
            with self.assertRaises(PairingDiagnosticError):
                await client._repair_verified_bond(device, None, time.monotonic() + 60)
            remove.assert_awaited_once()

    async def test_windows_repair_removes_both_transports_of_only_selected_container(
        self,
    ):
        client = self.new_client()
        device = SimpleNamespace(address="40:01:02:03:04:05")
        ble = BondDevice("ble", "400102030405", "phone", "ble")
        classic = BondDevice("classic", "AABBCCDDEEFF", "phone", "classic")
        other = BondDevice("unrelated", "400102030409", "other", "ble")
        with (
            patch("reverse_gatt_client.sys.platform", "win32"),
            patch(
                "reverse_gatt_client.list_bonded_devices",
                new=AsyncMock(side_effect=[[ble, classic, other], [other]]),
            ),
            patch("reverse_gatt_client.unpair_device", new=AsyncMock()) as remove,
        ):
            await client._remove_verified_peer_bonds(device, None)
            self.assertEqual(
                [c.args[0] for c in remove.await_args_list], [ble, classic]
            )


if __name__ == "__main__":
    unittest.main()
