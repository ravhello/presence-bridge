from __future__ import annotations

import asyncio
import base64
import json
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa
from observer import (
    APP_PAIRING_TRANSPORT,
    BlePresenceObserver,
    ObserverConfig,
    encrypt_pairing_result,
    normalize_address,
    scan_session_is_stale,
    select_irk_record_for_address,
    select_new_irk_records,
    verified_app_identity_payload,
)
from protocol import PairingLink
from reverse_gatt_client import ReverseGattResult


class BlePresenceObserverTest(unittest.TestCase):
    def test_app_pairing_uses_iphone_as_the_peripheral(self) -> None:
        self.assertEqual(APP_PAIRING_TRANSPORT, "iphone_peripheral")

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

    def test_verified_app_identity_omits_oversized_diagnostics(self) -> None:
        payload = verified_app_identity_payload(
            {
                "irk": "AA" * 16,
                "registry_leaf": "AABBCCDDEEFF",
                "recovery": "session_scoped_existing_windows_bond",
            }
        )

        self.assertEqual(
            payload,
            {"irk": "AA" * 16, "claim_verified": True},
        )
        self.assertLessEqual(
            len(__import__("json").dumps(payload, separators=(",", ":")).encode()),
            190,
        )

    def test_pairing_result_reports_rsa_oaep_capacity(self) -> None:
        private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        public_der = private_key.public_key().public_bytes(
            serialization.Encoding.DER,
            serialization.PublicFormat.SubjectPublicKeyInfo,
        )

        with self.assertRaisesRegex(ValueError, "too large for RSA-OAEP"):
            encrypt_pairing_result(
                base64.b64encode(public_der).decode("ascii"),
                {"oversized": "x" * 256},
            )


class ScannerPairingCoordinationTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        with patch("observer.MqttPublisher", return_value=Mock()):
            self.observer = BlePresenceObserver(
                SimpleNamespace(observer_id="dell_cucina")
            )

    async def test_pairing_waits_until_scanner_is_stopped(self) -> None:
        self.observer._scanner_stopped.clear()
        pause_task = asyncio.create_task(self.observer._pause_scanner_for_pairing())
        await asyncio.sleep(0)
        self.assertTrue(self.observer._scanner_pause_requested.is_set())
        self.assertFalse(pause_task.done())

        self.observer._scanner_stopped.set()
        await pause_task
        self.observer._resume_scanner_after_pairing()
        self.assertFalse(self.observer._scanner_pause_requested.is_set())

    async def test_encrypted_exchange_receipt_waits_for_ha_commit(self) -> None:
        self.observer.config.interactive_pairing_task = "fake-task"
        self.observer._pause_scanner_for_pairing = AsyncMock()
        self.observer._resume_scanner_after_pairing = Mock()
        result_sent = asyncio.Event()
        receipts = []

        async def exchange(link, _timeout, gatt, progress, *, completion_receipt=False):
            if completion_receipt:
                receipts.append(link.session_id)
            else:
                progress(
                    "receiver_proximity_confirmed",
                    "near",
                    attempt_expires_at=time.time() + 300,
                )
            return ReverseGattResult(
                address="11:22:33:44:55:66",
                name="iPhone",
                secure_exchange_complete=True,
            )

        self.observer._run_interactive_app_pairing = exchange

        def publish(topic, *_args, **_kwargs):
            if topic == "pairing/result":
                result_sent.set()

        self.observer.mqtt.publish_bridge_json.side_effect = publish
        key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        public = base64.b64encode(
            key.public_key().public_bytes(
                serialization.Encoding.DER,
                serialization.PublicFormat.SubjectPublicKeyInfo,
            )
        ).decode()
        link = PairingLink(
            session_id="abcdefghijklmnop",
            observer_id="dell_cucina",
            expires_at=int(time.time()) + 60,
            secret=bytes(range(32)),
        )
        with patch(
            "observer.read_windows_private_ble_irks",
            return_value=[{"irk": "11" * 16, "registry_leaf": "112233445566"}],
        ):
            task = asyncio.create_task(
                self.observer._run_app_pairing_session(link, public, 60, {})
            )
            try:
                await asyncio.wait_for(result_sent.wait(), 3)
                self.assertEqual(receipts, [])
                self.assertFalse(task.done())
                self.observer._ha_pairing_committed.set()
                await asyncio.wait_for(task, 3)
                self.assertEqual(receipts, [link.session_id])
            finally:
                task.cancel()

    async def test_registry_only_peer_cannot_publish_an_identity(self) -> None:
        self.observer.config.interactive_pairing_task = "fake-task"
        self.observer._pause_scanner_for_pairing = AsyncMock()
        self.observer._resume_scanner_after_pairing = Mock()
        self.observer._run_interactive_app_pairing = AsyncMock(
            return_value=ReverseGattResult(
                address="11:22:33:44:55:66",
                name="iPhone",
                transport="existing_windows_bond",
            )
        )
        link = PairingLink(
            session_id="abcdefghijklmnop",
            observer_id="dell_cucina",
            expires_at=int(time.time()) + 60,
            secret=bytes(range(32)),
        )
        with patch("observer.read_windows_private_ble_irks", return_value=[]):
            await self.observer._run_app_pairing_session(link, "unused", 60, {})
        self.assertFalse(
            any(
                call.args[0] == "pairing/result"
                for call in self.observer.mqtt.publish_bridge_json.call_args_list
            )
        )
        self.assertTrue(
            any(
                call.args[1].get("detail_code") == "iphone_secure_exchange_missing"
                for call in self.observer.mqtt.publish_bridge_json.call_args_list
                if len(call.args) > 1 and isinstance(call.args[1], dict)
            )
        )

    async def test_helper_success_requires_encrypted_exchange_not_saved_bond(
        self,
    ) -> None:
        link = PairingLink(
            session_id="abcdefghijklmnop",
            observer_id="dell_cucina",
            expires_at=int(time.time()) + 60,
            secret=bytes(range(32)),
        )
        for proof in (None, False, "true", True):
            with self.subTest(proof=proof), tempfile.TemporaryDirectory() as directory:
                command = Path(directory) / "command.json"
                result = Path(directory) / "result.json"
                self.observer.config.interactive_pairing_command_path = str(command)
                self.observer.config.interactive_pairing_result_path = str(result)
                self.observer.config.interactive_pairing_task = "fake-task"

                async def spawn(*args, result=result, proof=proof, **kwargs):
                    if "/Run" in args:
                        result.write_text(
                            json.dumps(
                                {
                                    "session_id": link.session_id,
                                    "state": "success",
                                    "address": "11:22:33:44:55:66",
                                    "secure_exchange_complete": proof,
                                }
                            )
                        )
                    return SimpleNamespace(
                        returncode=0, communicate=AsyncMock(return_value=(b"", b""))
                    )

                with patch(
                    "observer.asyncio.create_subprocess_exec", side_effect=spawn
                ):
                    if proof is True:
                        peer = await self.observer._run_interactive_app_pairing(
                            link, 60, {}, Mock()
                        )
                        self.assertTrue(peer.secure_exchange_complete)
                    else:
                        with self.assertRaisesRegex(
                            RuntimeError, "encrypted Bluetooth"
                        ):
                            await self.observer._run_interactive_app_pairing(
                                link, 60, {}, Mock()
                            )

    async def test_stale_ha_confirmation_is_ignored(self) -> None:
        task = asyncio.create_task(asyncio.Event().wait())
        self.observer._active_pairing_task = task
        self.observer._active_pairing_session_id = "current_session_1234"
        loop = asyncio.create_task(self.observer._pairing_command_loop())
        try:
            await self.observer._pairing_commands.put(
                {"action": "complete", "session_id": "old_session_1234"}
            )
            await asyncio.sleep(0)
            self.assertFalse(self.observer._ha_pairing_committed.is_set())
            await self.observer._pairing_commands.put(
                {"action": "complete", "session_id": "current_session_1234"}
            )
            await asyncio.sleep(0)
            self.assertTrue(self.observer._ha_pairing_committed.is_set())
        finally:
            loop.cancel()
            task.cancel()
            await asyncio.gather(loop, task, return_exceptions=True)

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

    async def test_pairing_heartbeat_keeps_receiver_online(self) -> None:
        progress = {
            "state": "waiting_for_app",
            "message": "Waiting",
            "extra": {"detail_code": "waiting_for_iphone_advertisement"},
        }
        self.observer.stop_event.set()

        await self.observer._pairing_heartbeat_loop(
            "abcdefghijklmnop",
            progress,
        )

        self.observer.mqtt.publish_bridge_json.assert_not_called()

        self.observer.stop_event.clear()
        self.observer._sleep_or_stop = Mock(
            side_effect=lambda _delay: self._stop_after_heartbeat()
        )
        await self.observer._pairing_heartbeat_loop(
            "abcdefghijklmnop",
            progress,
        )
        self.observer.mqtt.publish_bridge_json.assert_any_call(
            "status",
            self.observer.mqtt.status_payload(online=True),
        )
        self.observer.mqtt.publish.assert_any_call("availability", "online")

    async def _stop_after_heartbeat(self) -> None:
        self.observer.stop_event.set()


if __name__ == "__main__":
    unittest.main()
