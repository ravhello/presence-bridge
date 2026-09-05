from __future__ import annotations

import asyncio
import base64
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa
from observer import (
    BlePresenceObserver,
    ObserverConfig,
    encrypt_pairing_result,
    normalize_address,
    scan_session_is_stale,
    select_irk_record_for_address,
    select_new_irk_records,
)


class BlePresenceObserverTest(unittest.TestCase):
    def test_normalize_address(self) -> None:
        self.assertEqual(
            normalize_address("b0:81:84:ed:b2:56"),
            "B0:81:84:ED:B2:56",
        )
        self.assertEqual(normalize_address("not-an-address"), "")

    def test_scanner_stale_timeout_has_bounded_default(self) -> None:
        self.assertEqual(
            ObserverConfig.__dataclass_fields__["scanner_stale_timeout"].default,
            120.0,
        )
        self.assertFalse(scan_session_is_stale(100.0, 180.0, 250.0, 120.0))
        self.assertTrue(scan_session_is_stale(100.0, 180.0, 300.0, 120.0))

    def test_new_irk_records_are_unique_and_exclude_the_baseline(self) -> None:
        baseline = [{"irk": "00" * 16, "registry_leaf": "AABBCCDDEEFF"}]
        current = [
            *baseline,
            {"irk": "11" * 16, "registry_leaf": "112233445566"},
            {"irk": "11" * 16, "registry_leaf": "665544332211"},
        ]
        selected = select_new_irk_records(baseline, current)
        self.assertEqual(len(selected), 1)
        self.assertEqual(selected[0]["irk"], "11" * 16)

    def test_selects_existing_irk_by_registry_identity_address(self) -> None:
        row = {"irk": "11" * 16, "registry_leaf": "112233445566"}
        self.assertIs(
            select_irk_record_for_address([row], "11:22:33:44:55:66"),
            row,
        )

    def test_selects_existing_irk_by_resolvable_private_address(self) -> None:
        row = {"irk": "00" * 16, "registry_leaf": "112233445566"}
        self.assertIs(
            select_irk_record_for_address([row], "40:01:02:0A:C4:A6"),
            row,
        )

    def test_pairing_result_is_encrypted_for_home_assistant(self) -> None:
        private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        public_der = private_key.public_key().public_bytes(
            serialization.Encoding.DER,
            serialization.PublicFormat.SubjectPublicKeyInfo,
        )
        encoded = encrypt_pairing_result(
            __import__("base64").b64encode(public_der).decode("ascii"),
            {"irk": "AA" * 16},
        )
        plaintext = private_key.decrypt(
            __import__("base64").b64decode(encoded),
            padding.OAEP(
                mgf=padding.MGF1(algorithm=hashes.SHA256()),
                algorithm=hashes.SHA256(),
                label=None,
            ),
        )
        self.assertEqual(__import__("json").loads(plaintext)["irk"], "AA" * 16)


class ScannerPairingCoordinationTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        with patch("observer.MqttPublisher", return_value=Mock()):
            self.observer = BlePresenceObserver(SimpleNamespace())

    async def test_pairing_waits_until_scanner_is_stopped(self) -> None:
        self.observer._scanner_stopped.clear()
        pause_task = asyncio.create_task(
            self.observer._pause_scanner_for_pairing()
        )
        await asyncio.sleep(0)
        self.assertTrue(self.observer._scanner_pause_requested.is_set())
        self.assertFalse(pause_task.done())

        self.observer._scanner_stopped.set()
        await pause_task
        self.observer._resume_scanner_after_pairing()
        self.assertFalse(self.observer._scanner_pause_requested.is_set())

    async def test_duplicate_active_pairing_command_is_ignored(self) -> None:
        private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        public_key = base64.b64encode(
            private_key.public_key().public_bytes(
                serialization.Encoding.DER,
                serialization.PublicFormat.SubjectPublicKeyInfo,
            )
        ).decode("ascii")
        started = asyncio.Event()

        async def active_session(*_args: object) -> None:
            started.set()
            await asyncio.Event().wait()

        self.observer._run_pairing_session = Mock(side_effect=active_session)
        loop_task = asyncio.create_task(self.observer._pairing_command_loop())
        payload = {
            "action": "start",
            "session_id": "abcdefghijklmnop",
            "public_key": public_key,
            "timeout_seconds": 60,
        }
        try:
            await self.observer._pairing_commands.put(payload)
            await started.wait()
            await self.observer._pairing_commands.put(dict(payload))
            await asyncio.sleep(0)
            await asyncio.sleep(0)
            self.assertEqual(self.observer._run_pairing_session.call_count, 1)
        finally:
            loop_task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await loop_task
            if self.observer._active_pairing_task is not None:
                self.observer._active_pairing_task.cancel()
                with self.assertRaises(asyncio.CancelledError):
                    await self.observer._active_pairing_task


if __name__ == "__main__":
    unittest.main()
