"""Validate selected-person ownership and preservation of other trackers."""

import ast
from pathlib import Path
from types import SimpleNamespace
from unittest import IsolatedAsyncioTestCase
from unittest.mock import AsyncMock


class PersonLinkTest(IsolatedAsyncioTestCase):
    def setUp(self):
        source = (
            Path(__file__).parents[1]
            / "custom_components/presence_bridge/person_link.py"
        )
        tree = ast.parse(source.read_text())
        self.owners = []
        ns = {
            "HomeAssistantError": RuntimeError,
            "person": SimpleNamespace(persons_with_entity=lambda *_: self.owners),
        }
        exec(
            compile(
                ast.Module(
                    body=[n for n in tree.body if isinstance(n, ast.AsyncFunctionDef)],
                    type_ignores=[],
                ),
                str(source),
                "exec",
            ),
            ns,
        )
        self.link = ns["async_link_tracker"]
        self.attrs = {
            "id": "target",
            "editable": True,
            "device_trackers": ["device_tracker.gps"],
        }
        self.collection = SimpleNamespace(
            async_items=lambda: [
                {"id": "target", "device_trackers": self.attrs["device_trackers"]}
            ],
            async_update_item=AsyncMock(),
        )
        self.hass = SimpleNamespace(
            states=SimpleNamespace(
                get=lambda _: SimpleNamespace(attributes=self.attrs)
            ),
            data={"person": [None, self.collection]},
        )

    async def test_append_preserves_existing_gps_and_wifi(self):
        self.attrs["device_trackers"].append("device_tracker.wifi")
        await self.link(self.hass, "person.test", "device_tracker.ble")
        self.collection.async_update_item.assert_awaited_once_with(
            "target",
            {
                "device_trackers": [
                    "device_tracker.gps",
                    "device_tracker.wifi",
                    "device_tracker.ble",
                ]
            },
        )

    async def test_already_linked_is_idempotent(self):
        await self.link(self.hass, "person.test", "device_tracker.gps")
        self.collection.async_update_item.assert_not_awaited()

    async def test_never_steal_another_person_tracker(self):
        self.owners.append("person.other")
        with self.assertRaisesRegex(RuntimeError, "tracker_already_owned"):
            await self.link(self.hass, "person.test", "device_tracker.ble")
        self.collection.async_update_item.assert_not_awaited()

    async def test_yaml_person_is_not_rewritten(self):
        self.attrs["editable"] = False
        with self.assertRaisesRegex(RuntimeError, "person_managed_in_yaml"):
            await self.link(self.hass, "person.test", "device_tracker.ble")
        self.collection.async_update_item.assert_not_awaited()
