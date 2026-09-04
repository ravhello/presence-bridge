"""Keep every public Presence Bridge version in sync."""

from __future__ import annotations

import ast
import json
import tomllib
from pathlib import Path

ROOT = Path(__file__).parents[1]


def _observer_version() -> str:
    tree = ast.parse(
        (ROOT / "bridge" / "windows" / "observer.py").read_text(encoding="utf-8")
    )
    for node in tree.body:
        if (
            isinstance(node, ast.Assign)
            and any(
                isinstance(target, ast.Name) and target.id == "BRIDGE_VERSION"
                for target in node.targets
            )
            and isinstance(node.value, ast.Constant)
            and isinstance(node.value.value, str)
        ):
            return node.value.value
    raise AssertionError("BRIDGE_VERSION is missing from the Windows observer")


def test_public_versions_match() -> None:
    project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    manifest = json.loads(
        (ROOT / "custom_components" / "presence_bridge" / "manifest.json").read_text(
            encoding="utf-8"
        )
    )

    assert project["project"]["version"] == manifest["version"] == _observer_version()
