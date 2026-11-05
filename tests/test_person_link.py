"""Person assignment is read-only, live, and independent of private collections."""

import ast
import copy
import importlib.util
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).parents[1]
spec = importlib.util.spec_from_file_location(
    "presence_person_link", ROOT / "custom_components/presence_bridge/person_link.py"
)
link = importlib.util.module_from_spec(spec)
spec.loader.exec_module(link)
person_link_status = link.person_link_status


def person_hass():
    selected = SimpleNamespace(
        entity_id="person.test",
        attributes={
            "editable": True,
            "device_trackers": ["device_tracker.gps", "device_tracker.wifi"],
        },
    )
    other = SimpleNamespace(
        entity_id="person.other", attributes={"device_trackers": []}
    )
    states = {row.entity_id: row for row in (selected, other)}
    hass = SimpleNamespace(
        states=SimpleNamespace(
            get=states.get, async_all=lambda _: list(states.values())
        )
    )
    return hass, states, selected, other


def test_assignment_requires_owner_action_without_private_data_or_mutation():
    hass, states, _, _ = person_hass()
    before = copy.deepcopy(states)
    assert (
        person_link_status(hass, "person.test", "device_tracker.ble")
        == "manual_configuration_required"
    )
    assert states == before
    assert not hasattr(hass, "data")


def test_existing_and_new_manual_links_update_immediately_without_repair():
    hass, _, selected, _ = person_hass()
    selected.attributes["device_trackers"].append("device_tracker.ble")
    assert person_link_status(hass, "person.test", "device_tracker.ble") == "linked"
    selected.attributes["device_trackers"].remove("device_tracker.ble")
    assert (
        person_link_status(hass, "person.test", "device_tracker.ble")
        == "manual_configuration_required"
    )
    assert selected.attributes["device_trackers"] == [
        "device_tracker.gps",
        "device_tracker.wifi",
    ]


@pytest.mark.parametrize("already_selected", [True, False])
def test_another_owner_is_visible_but_never_changed(already_selected):
    hass, _, selected, other = person_hass()
    other.attributes["device_trackers"].append("device_tracker.ble")
    if already_selected:
        selected.attributes["device_trackers"].append("device_tracker.ble")
    before = copy.deepcopy(other.attributes)
    assert (
        person_link_status(hass, "person.test", "device_tracker.ble")
        == "tracker_already_owned"
    )
    assert other.attributes == before


def test_yaml_person_is_read_only_and_an_existing_yaml_link_is_recognized():
    hass, _, selected, _ = person_hass()
    selected.attributes["editable"] = False
    assert (
        person_link_status(hass, "person.test", "device_tracker.ble")
        == "person_managed_in_yaml"
    )
    selected.attributes["device_trackers"].append("device_tracker.ble")
    assert person_link_status(hass, "person.test", "device_tracker.ble") == "linked"


def test_missing_person_and_pending_tracker_are_not_reported_as_success():
    hass, states, _, _ = person_hass()
    assert person_link_status(hass, "person.test", None) == "tracker_pending"
    states.pop("person.test")
    assert (
        person_link_status(hass, "person.test", "device_tracker.ble")
        == "person_not_ready"
    )


def test_tracker_setup_only_subscribes_and_never_links_or_saves():
    source = ROOT / "custom_components/presence_bridge/device_tracker.py"
    tree = ast.parse(source.read_text(encoding="utf-8"))
    cls = next(node for node in tree.body if isinstance(node, ast.ClassDef))
    assert not any(
        getattr(node, "name", "") == "_async_link_person" for node in cls.body
    )
    setup = next(
        node for node in cls.body if getattr(node, "name", "") == "async_added_to_hass"
    )
    assert len(setup.body) == 1
    assert isinstance(setup.body[0], ast.Expr)
    assert setup.body[0].value.func.attr == "async_on_remove"
    for filename in ("person_link.py", "device_tracker.py"):
        text = (source.parent / filename).read_text(encoding="utf-8")
        assert 'hass.data.get("person")' not in text
        assert "async_update_item" not in text
        assert "person_link_completed" not in text


def test_diagnostics_omit_the_identifying_tracker_entity_id():
    source = ROOT / "custom_components/presence_bridge/coordinator.py"
    tree = ast.parse(source.read_text(encoding="utf-8"))
    cls = next(node for node in tree.body if isinstance(node, ast.ClassDef))
    method = next(
        node for node in cls.body if getattr(node, "name", "") == "diagnostics_payload"
    )
    ns = {"Any": object}
    exec(compile(ast.Module(body=[method], type_ignores=[]), str(source), "exec"), ns)
    payload = {
        "observers": [],
        "identities": [
            {
                "identity_id": "private_id",
                "person_entity_id": "person.test",
                "label": "private name",
                "tracker_entity_id": "device_tracker.private_name",
            }
        ],
        "people": [],
        "areas": [],
        "pairing": {},
    }
    coordinator = SimpleNamespace(public_payload=lambda **_: copy.deepcopy(payload))
    report = ns["diagnostics_payload"](coordinator)
    assert "tracker_entity_id" not in report["identities"][0]
    assert "private_name" not in str(report)
