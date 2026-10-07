"""Exercise discovery policy as well as authenticated post-bond recovery."""

from __future__ import annotations

import asyncio
import json
import time
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from protocol import PairingLink, claim_proof, pairing_service_uuid
from reverse_gatt_client import ReverseGattError, ReverseGattPairingClient


class PostBondDiscoveryTest(unittest.IsolatedAsyncioTestCase):
    async def run_recovery(self, route, *, stale_cache=False):
        link = PairingLink(
            "abcdefghijklmnopQRSTUVWX",
            "receiver",
            int(time.time()) + 180,
            bytes(range(32)),
        )
        device = SimpleNamespace(address="76:01:02:03:04:05", name="iPhone")
        advertisement = SimpleNamespace(
            service_uuids=[pairing_service_uuid(link)],
            service_data={},
            rssi=-60,
        )
        session = {
            "v": link.version,
            "sid": link.session_id,
            "oid": link.observer_id,
            "exp": link.expires_at,
        }
        proofs, leases, transports, routes, events = [], [], [], [], []

        class Policy:
            reused_bond = False
            allow_link_recovery = True
            allow_bond_repair = True
            calls = 0

            async def __call__(self, *_args):
                self.reused_bond = self.calls > 0
                self.calls += 1
                return True

        policy = Policy()
        client = ReverseGattPairingClient(
            service_uuid="service",
            session_uuid="session",
            claim_uuid="claim",
            result_uuid="result",
            pairing_probe=policy,
            progress_callback=lambda code, _message: events.append(code),
        )

        async def find(callback, **_kwargs):
            self.assertTrue(callback(device, advertisement))
            return device

        async def connect(_device, **kwargs):
            self.assertIs(_device, device)
            routes.append(
                (
                    kwargs["pair_before_discovery"],
                    kwargs["use_cached_services"],
                    kwargs["filter_services"],
                )
            )
            if transports:
                if route == "cancelled":
                    raise asyncio.CancelledError()
                if route == "none":
                    raise TimeoutError()
                if route == "full" and (
                    kwargs["filter_services"] or kwargs["pair_before_discovery"]
                ):
                    raise TimeoutError()
                if route == "cached" and not kwargs["use_cached_services"]:
                    raise TimeoutError()
            number = len(transports)
            native = SimpleNamespace(is_connected=True)

            async def read(uuid):
                proofs.append((number, uuid))
                if stale_cache and number == 1:
                    raise OSError("Cached handles no longer readable")
                payload = dict(session)
                if uuid == "claim":
                    payload["proof"] = claim_proof(link)
                return json.dumps(payload).encode()

            async def write(*_args, **_kwargs):
                leases.append(client._completion_deadline)
                if number == 0:
                    native.is_connected = False
                    error = OSError("Authenticated channel closed")
                    error.winerror = -2147023673
                    raise error

            native.read_gatt_char = AsyncMock(side_effect=read)
            native.write_gatt_char = AsyncMock(side_effect=write)
            transports.append(native)
            return native

        with (
            patch(
                "reverse_gatt_client.BleakScanner.find_device_by_filter",
                AsyncMock(side_effect=find),
            ),
            patch.object(client, "_connect_candidate", AsyncMock(side_effect=connect)),
            patch.object(
                client, "_gatt_inventory", return_value="session,claim,result"
            ),
            patch.object(client, "_require_ack_encryption") as protect,
            patch.object(client, "_release_client", AsyncMock()) as release,
            patch.object(client, "_remove_verified_peer_bonds", AsyncMock()) as remove,
            patch("reverse_gatt_client.asyncio.sleep", AsyncMock()),
        ):
            if route in {"none", "cancelled"}:
                expected = (
                    asyncio.CancelledError if route == "cancelled" else ReverseGattError
                )
                with self.assertRaises(expected):
                    await client.async_pair(link, 60)
                self.assertNotIn("iphone_claim_accepted", events)
            else:
                result = await client.async_pair(link, 60)
                self.assertTrue(result.secure_exchange_complete)
                self.assertEqual(len(set(leases)), 1)
                self.assertEqual(
                    proofs[-2:],
                    [(len(transports) - 1, "session"), (len(transports) - 1, "claim")],
                )
            remove.assert_not_awaited()
            self.assertEqual(release.await_count, len(transports))
            self.assertTrue(
                all(c.kwargs["require_authentication"] for c in protect.call_args_list)
            )
        return routes, client, transports

    async def test_authenticated_disconnect_reuses_services_but_reads_fresh_proofs(
        self,
    ):
        routes, client, _ = await self.run_recovery("cached")
        self.assertEqual(
            routes,
            [
                (False, False, True),
                (True, False, False),
                (False, True, True),
            ],
        )
        self.assertEqual(client._fresh_discovery_addresses, set())

    async def test_unavailable_filtered_lookup_falls_back_to_complete_discovery(self):
        routes, _, _ = await self.run_recovery("full")
        self.assertIn((False, False, False), routes)

    async def test_unreadable_cached_payload_retries_authenticated_discovery(self):
        routes, client, transports = await self.run_recovery("all", stale_cache=True)
        self.assertEqual(
            routes,
            [
                (False, False, True),
                (True, False, False),
                (True, False, False),
            ],
        )
        self.assertIn("76:01:02:03:04:05", client._fresh_discovery_addresses)
        transports[1].write_gatt_char.assert_not_awaited()

    async def test_failed_discovery_is_bounded_and_preserves_new_bond(self):
        routes, _, _ = await self.run_recovery("none")
        self.assertLessEqual(len(routes), 9)

    async def test_cancellation_stops_recovery_without_another_route(self):
        routes, _, _ = await self.run_recovery("cancelled")
        self.assertEqual(len(routes), 2)
