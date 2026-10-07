"""A protected-attribute trigger cannot substitute for secure enrollment."""

from __future__ import annotations

import asyncio
import json
import time
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

from bleak.exc import BleakGATTProtocolError
from protocol import PairingLink, claim_proof
from reverse_gatt_client import PairingDiagnosticError, ReverseGattPairingClient


class ProtectedPairingTriggerTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.progress = Mock()
        self.pairer = ReverseGattPairingClient(
            service_uuid="service",
            session_uuid="session",
            claim_uuid="claim",
            result_uuid="result",
            progress_callback=self.progress,
        )
        self.client = SimpleNamespace(write_gatt_char=AsyncMock())

    async def test_security_challenge_allows_only_authentication_or_encryption_errors(
        self,
    ):
        for code in (0x05, 0x0F):
            self.client.write_gatt_char.side_effect = BleakGATTProtocolError(code)
            await self.pairer._prepare_ios_protected_access(
                self.client, b"proof", time.monotonic() + 1
            )
            self.assertFalse(self.pairer._secure_bond_confirmed)
            self.assertEqual(
                self.progress.call_args.args[0],
                "iphone_protected_access_requires_pairing",
            )

    async def test_other_att_errors_are_not_treated_as_security_challenges(self):
        self.client.write_gatt_char.side_effect = BleakGATTProtocolError(0x01)
        with self.assertRaises(PairingDiagnosticError) as raised:
            await self.pairer._prepare_ios_protected_access(
                self.client, b"proof", time.monotonic() + 1
            )
        self.assertEqual(
            raised.exception.detail_code, "iphone_protected_access_rejected"
        )

    async def test_success_is_not_enrollment(self):
        await self.pairer._prepare_ios_protected_access(
            self.client, b"proof", time.monotonic() + 1
        )
        self.client.write_gatt_char.assert_awaited_once_with(
            "result", b"proof", response=True
        )
        self.assertFalse(self.pairer._secure_bond_confirmed)
        self.assertNotIn(
            "iphone_claim_accepted",
            [call.args[0] for call in self.progress.call_args_list],
        )

    async def test_timed_out_trigger_is_drained_and_never_retried(self):
        cancelled = asyncio.Event()

        async def stalled(*_args, **_kwargs):
            try:
                await asyncio.Event().wait()
            finally:
                cancelled.set()

        self.client.write_gatt_char.side_effect = stalled
        with self.assertRaises(PairingDiagnosticError) as raised:
            await self.pairer._prepare_ios_protected_access(
                self.client, b"proof", time.monotonic() + 0.02
            )
        self.assertEqual(
            raised.exception.detail_code, "iphone_protected_access_timeout"
        )
        self.assertTrue(cancelled.is_set())
        self.assertEqual(self.client.write_gatt_char.await_count, 1)

    async def test_trigger_precedes_pairing_but_final_ack_requires_authentication(self):
        link = PairingLink(
            session_id="abcdefghijklmnopQRSTUVWX",
            observer_id="receiver",
            expires_at=int(time.time()) + 180,
            secret=bytes(range(32)),
        )
        events = []

        class Policy:
            accepts_protected_access_trigger = True
            reused_bond = False

            async def __call__(
                self, client, candidate_link, deadline, *, prepare_protected_access
            ):
                await prepare_protected_access()
                events.append("native_verified")
                return True

        self.pairer.pairing_probe = Policy()
        session = {
            "v": link.version,
            "sid": link.session_id,
            "oid": link.observer_id,
            "exp": link.expires_at,
        }

        async def read(uuid):
            payload = dict(session)
            if uuid == "claim":
                payload["proof"] = claim_proof(link)
            return json.dumps(payload).encode()

        async def write(*_args, **_kwargs):
            events.append("write")
            if events == ["write"]:
                raise BleakGATTProtocolError(0x0F)

        native = SimpleNamespace(
            read_gatt_char=AsyncMock(side_effect=read),
            write_gatt_char=AsyncMock(side_effect=write),
        )
        device = SimpleNamespace(address="AA:BB:CC:DD:EE:FF", name="iPhone")
        with (
            patch.object(
                self.pairer, "_open_candidate", new=AsyncMock(return_value=native)
            ),
            patch.object(self.pairer, "_release_client", new=AsyncMock()),
            patch.object(
                self.pairer,
                "_require_ack_encryption",
                side_effect=lambda *_a, **_k: events.append("require_authentication"),
            ) as protect,
        ):
            result = await self.pairer._pair_candidate(
                device, link, 5, allow_bond_reset=False
            )
        self.assertTrue(result.secure_exchange_complete)
        self.assertEqual(
            events, ["write", "native_verified", "require_authentication", "write"]
        )
        protect.assert_called_once_with(native, require_authentication=True)
