"""Exercise the actual HA removal methods without a running HA test server."""

import ast
import asyncio
import hashlib
import json
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
        tree = ast.parse(source.read_text(encoding="utf-8"))
        methods = {
            "async_remove_identity",
            "_async_remove_identity_locked",
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
        c._cipher_cache = {self.key: object()}
        c.store = SimpleNamespace(async_save=AsyncMock())
        c.hass = object()
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
