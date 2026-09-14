from __future__ import annotations

import asyncio
import json
import tempfile
import time
import unittest
from enum import IntEnum
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

import numeric_pairing_probe as probe
from protocol import PairingLink
from reverse_gatt_client import PairingDiagnosticError


class Kind(IntEnum):
    CONFIRM_ONLY = 1
    CONFIRM_PIN_MATCH = 8


class Level(IntEnum):
    NONE = 1
    ENCRYPTION = 2
    ENCRYPTION_AND_AUTHENTICATION = 3


class Status(IntEnum):
    PAIRED = 0
    FAILED = 1


class NumericPairingTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.done = asyncio.Event()
        self.args = SimpleNamespace(
            pairing_kind=Kind.CONFIRM_PIN_MATCH,
            pin="123456",
            accept=Mock(),
            get_deferral=Mock(return_value=SimpleNamespace(complete=self.done.set)),
        )
        self.level = Level.ENCRYPTION_AND_AUTHENTICATION
        self.verified_level = Level.ENCRYPTION_AND_AUTHENTICATION
        self.verified_paired = True
        self.custom = SimpleNamespace(
            add_pairing_requested=Mock(),
            remove_pairing_requested=Mock(),
        )

        async def pair(kind, level):
            self.assertEqual(kind, Kind.CONFIRM_PIN_MATCH)
            self.assertEqual(level, Level.ENCRYPTION_AND_AUTHENTICATION)
            self.custom.add_pairing_requested.call_args.args[0](None, self.args)
            await self.done.wait()
            return SimpleNamespace(
                status=Status.PAIRED if self.args.accept.called else Status.FAILED,
                protection_level_used=self.level,
            )

        self.custom.pair_with_protection_level_async = AsyncMock(side_effect=pair)
        self.info = SimpleNamespace(
            pairing=SimpleNamespace(is_paired=False, can_pair=True, custom=self.custom)
        )
        self.client = SimpleNamespace(
            _backend=SimpleNamespace(
                _requester=SimpleNamespace(
                    device_information=SimpleNamespace(id="peer")
                )
            )
        )

        async def device_info(peer_id):
            self.assertEqual(peer_id, "peer")
            if not self.custom.pair_with_protection_level_async.await_count:
                return self.info
            return SimpleNamespace(
                pairing=SimpleNamespace(
                    is_paired=self.verified_paired, protection_level=self.verified_level
                )
            )

        for name, value in {
            "DeviceInformation": SimpleNamespace(
                create_from_id_async=AsyncMock(side_effect=device_info)
            ),
            "DevicePairingKinds": Kind,
            "DevicePairingProtectionLevel": Level,
            "DevicePairingResultStatus": Status,
        }.items():
            patcher = patch.object(probe, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)

    async def test_confirmed_numeric_comparison_requires_strong_encryption(self):
        confirm = AsyncMock(return_value=True)
        await probe.pair_with_numeric_comparison(
            self.client, confirm, time.monotonic() + 3
        )
        confirm.assert_awaited_once()
        self.assertEqual(confirm.await_args.args[0], "123456")
        self.args.accept.assert_called_once()
        self.custom.remove_pairing_requested.assert_called_once()

    async def test_rejection_never_accepts_or_falls_back(self):
        with self.assertRaises(PairingDiagnosticError):
            await probe.pair_with_numeric_comparison(
                self.client, AsyncMock(return_value=False), time.monotonic() + 3
            )
        self.args.accept.assert_not_called()
        self.custom.pair_with_protection_level_async.assert_awaited_once()

    async def test_just_works_ceremony_is_not_accepted(self):
        self.args.pairing_kind = Kind.CONFIRM_ONLY
        confirm = AsyncMock(return_value=True)
        with self.assertRaises(PairingDiagnosticError):
            await probe.pair_with_numeric_comparison(
                self.client, confirm, time.monotonic() + 3
            )
        confirm.assert_not_awaited()
        self.args.accept.assert_not_called()

    async def test_existing_bond_is_not_silently_reused(self):
        self.info.pairing.is_paired = True
        with self.assertRaises(PairingDiagnosticError) as raised:
            await probe.pair_with_numeric_comparison(
                self.client, AsyncMock(), time.monotonic() + 3
            )
        self.assertEqual(raised.exception.detail_code, "numeric_pairing_existing_bond")
        self.custom.pair_with_protection_level_async.assert_not_awaited()

    async def test_weaker_result_is_not_success(self):
        self.level = Level.ENCRYPTION
        self.verified_level = Level.ENCRYPTION
        with self.assertRaises(PairingDiagnosticError):
            await probe.pair_with_numeric_comparison(
                self.client, AsyncMock(return_value=True), time.monotonic() + 3
            )

    async def test_product_path_keeps_existing_authenticated_bond(self):
        self.info.pairing.is_paired = True
        self.info.pairing.protection_level = Level.ENCRYPTION_AND_AUTHENTICATION
        await probe.pair_with_numeric_comparison(
            self.client,
            AsyncMock(),
            time.monotonic() + 3,
            allow_authenticated_bond=True,
        )
        self.custom.pair_with_protection_level_async.assert_not_awaited()

    async def test_product_path_rejects_weak_saved_bond_without_unpair(self):
        self.info.pairing.is_paired = True
        self.info.pairing.protection_level = Level.ENCRYPTION
        with self.assertRaises(PairingDiagnosticError) as raised:
            await probe.pair_with_numeric_comparison(
                self.client,
                AsyncMock(),
                time.monotonic() + 3,
                allow_authenticated_bond=True,
            )
        self.assertEqual(
            raised.exception.detail_code, "numeric_pairing_saved_bond_weak"
        )
        self.custom.pair_with_protection_level_async.assert_not_awaited()

    async def test_stale_none_result_uses_fresh_authenticated_peer(self):
        self.level = Level.NONE
        await probe.pair_with_numeric_comparison(
            self.client, AsyncMock(return_value=True), time.monotonic() + 3
        )
        self.assertEqual(probe.DeviceInformation.create_from_id_async.await_count, 2)
        self.custom.pair_with_protection_level_async.assert_awaited_once()

    async def test_strong_result_cannot_override_fresh_weak_bond(self):
        self.verified_level = Level.ENCRYPTION
        with self.assertRaises(PairingDiagnosticError):
            await probe.pair_with_numeric_comparison(
                self.client, AsyncMock(return_value=True), time.monotonic() + 3
            )

    async def test_fresh_unpaired_peer_is_not_success(self):
        self.verified_paired = False
        with self.assertRaises(PairingDiagnosticError):
            await probe.pair_with_numeric_comparison(
                self.client, AsyncMock(return_value=True), time.monotonic() + 3
            )

    async def test_refresh_error_is_not_success(self):
        async def fail_refresh(peer_id):
            if self.custom.pair_with_protection_level_async.await_count:
                raise OSError("enumeration unavailable")
            return self.info

        probe.DeviceInformation.create_from_id_async.side_effect = fail_refresh
        with self.assertRaises(PairingDiagnosticError) as raised:
            await probe.pair_with_numeric_comparison(
                self.client, AsyncMock(return_value=True), time.monotonic() + 3
            )
        self.assertEqual(raised.exception.detail_code, "numeric_pairing_native_error")

    async def test_timeout_releases_deferral_without_accepting(self):
        async def wait_for_user(*_args):
            await asyncio.Event().wait()

        with self.assertRaises(PairingDiagnosticError) as raised:
            await probe.pair_with_numeric_comparison(
                self.client, wait_for_user, time.monotonic() + 0.05
            )
        self.assertEqual(raised.exception.detail_code, "numeric_pairing_timeout")
        self.assertTrue(self.done.is_set())
        self.args.accept.assert_not_called()
        self.custom.remove_pairing_requested.assert_called_once()

    async def test_cancellation_does_not_accept_late_confirmation(self):
        waiting = asyncio.Event()

        async def wait_for_user(*_args):
            waiting.set()
            await asyncio.Event().wait()

        task = asyncio.create_task(
            probe.pair_with_numeric_comparison(
                self.client, wait_for_user, time.monotonic() + 3
            )
        )
        await asyncio.wait_for(waiting.wait(), timeout=1)
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
        self.assertTrue(self.done.is_set())
        self.args.accept.assert_not_called()


class DiagnosticArmTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        self.link = PairingLink(
            session_id="abcdefghijklmnopQRSTUVWX",
            observer_id="dell_cucina",
            expires_at=int(time.time()) + 180,
            secret=bytes(range(32)),
        )
        self.probe = probe.NumericPairingProbe(self.root, self.write, Mock())

    @staticmethod
    def write(path, value):
        path.write_text(json.dumps(value), encoding="utf-8")

    def arm(self, **extra):
        self.write(
            self.root / probe.ARM_NAME,
            {
                "session_id": self.link.session_id,
                "observer_id": self.link.observer_id,
                "expires_at": time.time() + 180,
                **extra,
            },
        )

    async def test_unarmed_and_other_session_leave_normal_pairing_unchanged(self):
        with patch.object(
            probe, "pair_with_numeric_comparison", new=AsyncMock()
        ) as pair:
            self.assertFalse(await self.probe(None, self.link, time.monotonic() + 3))
            self.arm(session_id="another-session-value")
            self.assertFalse(await self.probe(None, self.link, time.monotonic() + 3))
            pair.assert_not_awaited()

    async def test_product_pairing_needs_no_private_arm_file(self):
        policy = probe.QRSessionPairing(self.root, self.write, Mock())
        with patch.object(
            probe, "pair_with_numeric_comparison", new=AsyncMock()
        ) as pair:
            self.assertTrue(await policy(None, self.link, time.monotonic() + 3))
            pair.assert_awaited_once()
            self.assertTrue(pair.await_args.kwargs["allow_authenticated_bond"])
            confirm = pair.await_args.args[1]
            self.assertTrue(await confirm("123456", time.monotonic() + 3))
            self.assertFalse(await confirm("123456", time.monotonic() - 1))
        self.assertFalse((self.root / probe.ARM_NAME).exists())

    async def test_arm_is_consumed_once_without_writing_identity(self):
        self.arm()
        with patch.object(
            probe, "pair_with_numeric_comparison", new=AsyncMock()
        ) as pair:
            self.assertTrue(await self.probe(None, self.link, time.monotonic() + 3))
            self.assertEqual(
                json.loads((self.root / probe.ARM_NAME).read_text())["state"],
                "consumed",
            )
            with self.assertRaises(PairingDiagnosticError):
                await self.probe(None, self.link, time.monotonic() + 3)
            restarted = probe.NumericPairingProbe(self.root, self.write, Mock())
            with self.assertRaises(PairingDiagnosticError):
                await restarted(None, self.link, time.monotonic() + 3)
            pair.assert_awaited_once()

    async def test_expired_arm_stops_instead_of_changing_pairing_method(self):
        self.arm(expires_at=time.time() - 1)
        with self.assertRaises(PairingDiagnosticError):
            await self.probe(None, self.link, time.monotonic() + 3)

    async def test_explicit_qr_scoped_consent_does_not_request_a_second_confirmation(
        self,
    ):
        self.arm(approval_mode="qr_authorized")
        self.probe.confirm = AsyncMock()

        async def native_pair(_client, confirm, deadline):
            self.assertTrue(await confirm("012345", deadline))
            self.assertFalse(await confirm("012345", time.monotonic() - 1))

        with patch.object(
            probe,
            "pair_with_numeric_comparison",
            new=AsyncMock(side_effect=native_pair),
        ):
            self.assertTrue(await self.probe(None, self.link, time.monotonic() + 3))
        self.probe.confirm.assert_not_awaited()
        self.assertFalse((self.root / probe.REQUEST_NAME).exists())
        self.assertEqual(
            json.loads((self.root / probe.ARM_NAME).read_text())["state"], "consumed"
        )

    async def test_unknown_consent_mode_is_rejected(self):
        self.arm(approval_mode="automatic_for_every_phone")
        with patch.object(
            probe, "pair_with_numeric_comparison", new=AsyncMock()
        ) as pair:
            with self.assertRaises(PairingDiagnosticError):
                await self.probe(None, self.link, time.monotonic() + 3)
            pair.assert_not_awaited()

    async def test_nonce_bound_explicit_confirmation_and_cleanup(self):
        created = asyncio.Event()

        def publish(path, value):
            self.write(path, value)
            created.set()

        self.probe.write_json = publish

        async def reply():
            path = self.root / probe.REQUEST_NAME
            await asyncio.wait_for(created.wait(), timeout=1)
            request = json.loads(path.read_text())
            self.write(
                self.root / probe.RESPONSE_NAME,
                {
                    "session_id": request["session_id"],
                    "nonce": request["nonce"],
                    "approved": True,
                },
            )

        task = asyncio.create_task(reply())
        try:
            self.assertTrue(
                await self.probe.confirm(
                    self.link.session_id, "123456", time.monotonic() + 2
                )
            )
        finally:
            await task
        self.assertFalse((self.root / probe.REQUEST_NAME).exists())
        self.assertFalse((self.root / probe.RESPONSE_NAME).exists())

    async def test_stale_reply_never_confirms(self):
        self.write(
            self.root / probe.RESPONSE_NAME,
            {"session_id": self.link.session_id, "nonce": "stale", "approved": True},
        )
        self.assertFalse(
            await self.probe.confirm(
                self.link.session_id, "123456", time.monotonic() + 0.05
            )
        )
