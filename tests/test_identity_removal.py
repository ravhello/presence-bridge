"""Exercise the actual HA removal methods without a running HA test server."""

import ast
import asyncio
import hashlib
import json
import os
import re
import secrets
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, Mock, patch


class CoordinatorRemovalTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        source = (
            Path(__file__).parents[1]
            / "custom_components/presence_bridge/coordinator.py"
        )
        source = Path(os.environ.get("PRESENCE_COORDINATOR_SOURCE", source))
        tree = ast.parse(source.read_text(encoding="utf-8"))
        methods = {
            "_async_publish",
            "async_remove_identity",
            "_async_remove_identity_locked",
            "_async_request_bond_removal",
            "_async_forget_identity",
            "_async_require_phone_reset",
            "_accept_bond_repair_offer",
            "async_repair_pairing",
            "_identity_removal_result_message",
        }
        selected = []
        for node in tree.body:
            if (
                isinstance(node, ast.FunctionDef)
                and node.name == "_observer_id_from_topic"
            ):
                selected.append(node)
            if (
                isinstance(node, ast.ClassDef)
                and node.name == "PresenceBridgeCoordinator"
            ):
                node.body = [
                    method
                    for method in node.body
                    if getattr(method, "name", None) in methods
                ]
                for method in node.body:
                    method.decorator_list = []
                selected.append(node)
        registry = Mock()
        registry.async_get_entity_id.return_value = None
        registry.async_get_device.return_value = None
        self.mqtt = SimpleNamespace(async_publish=AsyncMock())
        self.namespace = {
            "Any": Any,
            "asyncio": asyncio,
            "hashlib": hashlib,
            "json": json,
            "re": re,
            "_PAIRING_SESSION_ID_RE": re.compile(r"^[A-Za-z0-9_-]{16,96}$"),
            "DEFAULT_PAIRING_TIMEOUT": 600,
            "secrets": secrets,
            "time": time,
            "HomeAssistantError": RuntimeError,
            "mqtt": self.mqtt,
            "TOPIC_ROOT": "presence_bridge/v1/observers",
            "DOMAIN": "presence_bridge",
            "SIGNAL_STATE_UPDATED": "updated",
            "async_dispatcher_send": Mock(),
            "er": SimpleNamespace(async_get=lambda _: registry),
            "dr": SimpleNamespace(async_get=lambda _: registry),
        }
        exec(
            compile(ast.Module(body=selected, type_ignores=[]), str(source), "exec"),
            self.namespace,
        )
        self.coordinator = self.namespace["PresenceBridgeCoordinator"]()
        c = self.coordinator
        self.key = "11" * 16
        self.fingerprint = hashlib.sha256(bytes.fromhex(self.key)).hexdigest()
        self.identity = self.fingerprint[:16]
        c.memory = {
            "identities": {self.identity: {"irk": self.key, "paired_by": "dell"}}
        }
        c.observers = {
            "dell": SimpleNamespace(online=True, capabilities=["identity_removal"])
        }
        c._pairing_lock = asyncio.Lock()
        c._pairing_session = None
        c._removal_requests = {}
        c._bond_repair_offer = None
        c._async_start_pairing_locked = AsyncMock(return_value={"state": "preparing"})
        c._cipher_cache = {self.key: object()}
        c.store = SimpleNamespace(async_save=AsyncMock())
        c.hass = SimpleNamespace(
            config_entries=SimpleNamespace(async_entries=lambda _: [object()])
        )
        c.local_receiver = SimpleNamespace(receiver=None)
        c._rebuild_identity_states = Mock()
        c._set_pairing_state = Mock()
        c.pairing_public = {"identity_id": self.identity}
        c._decode_payload = lambda msg: msg.payload

    def reply(self, payload):
        self.coordinator._identity_removal_result_message(
            SimpleNamespace(
                payload=payload,
                topic=f"presence_bridge/v1/observers/{payload.get('observer_id')}/identity_removal/result",
            )
        )

    async def test_wait_for_correct_receiver_ack_before_clearing_ha(self):
        async def publish(hass, topic, raw, **kwargs):
            command = json.loads(raw)
            self.assertEqual(topic, "presence_bridge/v1/observers/dell/pairing/command")
            self.assertFalse(kwargs["retain"])
            self.assertNotIn(self.key, raw)
            self.assertIn(self.identity, self.coordinator.memory["identities"])
            self.reply({**command, "success": True, "observer_id": "wrong"})
            self.assertFalse(
                self.coordinator._removal_requests[command["request_id"]][
                    "future"
                ].done()
            )
            self.reply({**command, "success": True})

        self.mqtt.async_publish.side_effect = publish
        await self.coordinator.async_remove_identity(self.identity)
        self.assertNotIn(self.identity, self.coordinator.memory["identities"])
        self.assertNotIn(self.key, self.coordinator._cipher_cache)
        self.coordinator._set_pairing_state.assert_called_once()

    async def test_receiver_failure_preserves_association(self):
        async def publish(hass, topic, raw, **kwargs):
            self.reply({**json.loads(raw), "success": False, "message": "Windows busy"})

        self.mqtt.async_publish.side_effect = publish
        with self.assertRaisesRegex(RuntimeError, "Windows busy"):
            await self.coordinator.async_remove_identity(self.identity)
        self.assertIn(self.identity, self.coordinator.memory["identities"])
        self.coordinator.store.async_save.assert_not_awaited()
        self.assertNotIn("phone_bond_resets", self.coordinator.memory)

    async def test_confirmed_removal_persists_phone_preparation_for_exact_target(self):
        c = self.coordinator
        c.memory["identities"][self.identity]["person_entity_id"] = "person.test"

        async def publish(hass, topic, raw, **kwargs):
            self.reply({**json.loads(raw), "success": True})

        self.mqtt.async_publish.side_effect = publish
        await c.async_remove_identity(self.identity)
        self.assertEqual(c.memory["phone_bond_resets"], {"person.test:dell": True})
        c.store.async_save.assert_awaited()

    async def test_offline_or_old_receiver_cannot_silently_clear_ha(self):
        for online, capabilities in ((False, ["identity_removal"]), (True, [])):
            self.coordinator.observers["dell"] = SimpleNamespace(
                online=online, capabilities=capabilities
            )
            with self.assertRaises(RuntimeError):
                await self.coordinator.async_remove_identity(self.identity)
            self.assertIn(self.identity, self.coordinator.memory["identities"])
        self.mqtt.async_publish.assert_not_awaited()

    async def test_timeout_preserves_association_for_retry(self):
        with (
            patch("asyncio.wait_for", new=AsyncMock(side_effect=TimeoutError())),
            self.assertRaisesRegex(RuntimeError, "did not confirm"),
        ):
            await self.coordinator.async_remove_identity(self.identity)
        self.assertIn(self.identity, self.coordinator.memory["identities"])
        self.assertEqual(self.coordinator._removal_requests, {})

    def test_malformed_and_unsolicited_replies_are_ignored(self):
        self.coordinator._identity_removal_result_message(
            SimpleNamespace(payload=None, topic="unused")
        )
        self.reply({"request_id": "unknown", "observer_id": "dell", "success": True})

    def offer_repair(self):
        c = self.coordinator
        session = {
            "session_id": "failed_session_1234",
            "observer_id": "dell",
            "person_entity_id": "person.test",
        }
        offer = {
            "session_id": session["session_id"],
            "repair_id": "repair_token_12345",
            "fingerprint": self.fingerprint,
            "expires_at": time.time() + 600,
        }
        c.memory["identities"][self.identity]["person_entity_id"] = "person.test"
        visible = c._accept_bond_repair_offer({"bond_repair": offer}, session)
        c.pairing_public = visible
        self.assertNotIn("fingerprint", visible)
        return offer, session

    async def test_repair_clears_only_verified_identity_then_starts_same_person(self):
        offer, _ = self.offer_repair()
        c = self.coordinator
        c.memory["identities"]["unrelated"] = {"irk": "22" * 16}

        async def publish(hass, topic, raw, **kwargs):
            command = json.loads(raw)
            self.assertEqual(command["action"], "repair_pairing")
            self.assertEqual(command["repair_id"], offer["repair_id"])
            self.assertIn(self.identity, c.memory["identities"])
            self.reply({**command, "success": True})

        self.mqtt.async_publish.side_effect = publish
        result = await c.async_repair_pairing(offer["repair_id"])
        self.assertEqual(result["state"], "preparing")
        self.assertNotIn(self.identity, c.memory["identities"])
        self.assertIn("unrelated", c.memory["identities"])
        self.assertEqual(c.memory["phone_bond_resets"], {"person.test:dell": True})
        c._async_start_pairing_locked.assert_awaited_once_with(
            "person.test", "dell", 600, force_new=True
        )
        with self.assertRaises(RuntimeError):
            await c.async_repair_pairing(offer["repair_id"])

    async def test_orphan_bond_repair_does_not_require_ha_identity(self):
        offer, _ = self.offer_repair()
        c = self.coordinator
        c.memory["identities"].clear()

        async def publish(hass, topic, raw, **kwargs):
            self.reply({**json.loads(raw), "success": True})

        self.mqtt.async_publish.side_effect = publish
        await c.async_repair_pairing(offer["repair_id"])
        c._async_start_pairing_locked.assert_awaited_once()
        self.assertTrue(c.memory["phone_bond_resets"]["person.test:dell"])

    async def test_repair_failure_preserves_identity_and_does_not_issue_qr(self):
        offer, _ = self.offer_repair()

        async def publish(hass, topic, raw, **kwargs):
            self.reply({**json.loads(raw), "success": False, "message": "Keys remain"})

        self.mqtt.async_publish.side_effect = publish
        with self.assertRaisesRegex(RuntimeError, "Keys remain"):
            await self.coordinator.async_repair_pairing(offer["repair_id"])
        self.assertIn(self.identity, self.coordinator.memory["identities"])
        self.coordinator._async_start_pairing_locked.assert_not_awaited()
        self.assertNotIn("phone_bond_resets", self.coordinator.memory)

    async def test_expired_offer_new_attempt_and_changed_owner_are_rejected(self):
        for change in ("expired", "active", "owner", "wrong_token"):
            offer, _ = self.offer_repair()
            c = self.coordinator
            c._pairing_session = None
            token = offer["repair_id"]
            if change == "expired":
                c._bond_repair_offer["expires_at"] = 1
            if change == "active":
                c._pairing_session = {"new": True}
            if change == "owner":
                c.memory["identities"][self.identity]["person_entity_id"] = (
                    "person.other"
                )
            if change == "wrong_token":
                token = "not_the_offered_token"
            with self.assertRaises(RuntimeError):
                await c.async_repair_pairing(token)
        self.mqtt.async_publish.assert_not_awaited()

    def test_offer_for_different_person_or_malformed_offer_is_not_exposed(self):
        offer, session = self.offer_repair()
        c = self.coordinator
        for changes in (
            {"fingerprint": "bad"},
            {"expires_at": "bad"},
            {"expires_at": time.time() + 10000},
            {"session_id": "other"},
        ):
            self.assertEqual(
                c._accept_bond_repair_offer(
                    {"bond_repair": {**offer, **changes}}, session
                ),
                {},
            )
        session["person_entity_id"] = "person.other"
        self.assertEqual(
            c._accept_bond_repair_offer({"bond_repair": offer}, session), {}
        )
