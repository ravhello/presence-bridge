"""Explicit clean-room test setup; never used by normal phone enrollment."""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import platform
import time
from pathlib import Path
from types import SimpleNamespace
from uuid import UUID

from identity_removal import compact_address, list_bonded_devices


def _container(value: str) -> str:
    parsed = UUID(value)
    if not parsed.int:
        raise ValueError("An exact nonzero phone container is required")
    return str(parsed)


def _fingerprint(record: dict[str, str]) -> str:
    return hashlib.sha256(bytes.fromhex(record["irk"])).hexdigest()


async def reset_test_phone(
    anchor_address,
    known_container,
    journal_path,
    read_records,
    unpair_targets,
    *,
    inventory=list_bonded_devices,
):
    """Remove only the pinned test phone and prove both bond/key absence."""
    anchor = compact_address(anchor_address)
    if not anchor:
        raise ValueError("An exact classic Bluetooth anchor is required")
    container = _container(known_container)
    async with asyncio.timeout(70):
        journal = json.loads(journal_path.read_text()) if journal_path.exists() else {}
        if journal and journal.get("anchor") != anchor:
            raise RuntimeError("The test reset journal belongs to another phone")
        before = await inventory()
        records = await asyncio.to_thread(read_records)
        anchors = [
            d for d in before if d.transport == "classic" and d.address == anchor
        ]
        containers = {container, *journal.get("containers", [])}
        containers.update(_container(d.container_id) for d in anchors)
        addresses = {anchor, *journal.get("addresses", [])}
        targets = [
            d for d in before if d.address in addresses or d.container_id in containers
        ]
        addresses.update(d.address for d in targets)
        fingerprints = set(journal.get("fingerprints", []))
        fingerprints.update(
            _fingerprint(r)
            for r in records
            if compact_address(r["registry_leaf"]) in addresses
        )
        other_devices = {d.device_id for d in before if d not in targets}
        other_keys = {_fingerprint(r) for r in records} - fingerprints
        temporary = journal_path.with_suffix(".tmp")
        temporary.write_text(
            json.dumps(
                {
                    "anchor": anchor,
                    "addresses": sorted(addresses),
                    "containers": sorted(containers),
                    "fingerprints": sorted(fingerprints),
                }
            ),
            encoding="utf-8",
        )
        temporary.replace(journal_path)
        if targets:
            await unpair_targets(targets)
        for _attempt in range(8):
            remaining = await inventory()
            current_records = await asyncio.to_thread(read_records)
            current_keys = {_fingerprint(r) for r in current_records}
            if not other_devices.issubset(
                {d.device_id for d in remaining}
            ) or not other_keys.issubset(current_keys):
                raise RuntimeError(
                    "An unrelated bond or key changed during reset; no QR permitted"
                )
            bonds = [
                d
                for d in remaining
                if d.address in addresses or d.container_id in containers
            ]
            keys = [
                r
                for r in current_records
                if compact_address(r["registry_leaf"]) in addresses
                or _fingerprint(r) in fingerprints
            ]
            if not bonds and not keys:
                return {
                    "clean": True,
                    "removed_endpoints": len(targets),
                    "bonds_remaining": 0,
                    "keys_remaining": 0,
                    "unrelated_preserved": True,
                    "identity_ids": sorted(value[:16] for value in fingerprints),
                    "completed_at": time.time(),
                }
            await asyncio.sleep(0.5)
        raise RuntimeError("Phone bond or private key still present; no QR permitted")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--expected-host", required=True)
    parser.add_argument("--observer-id", required=True)
    parser.add_argument("--anchor-address", required=True)
    parser.add_argument("--container-id", required=True)
    parser.add_argument("--request-id", required=True)
    parser.add_argument("--result", type=Path, required=True)
    args = parser.parse_args()
    UUID(args.request_id)
    if platform.node().casefold() != args.expected_host.casefold():
        raise RuntimeError("Wrong Windows host; no Bluetooth changes made")
    from observer import (  # noqa: PLC0415 - load Windows service after the host check
        BlePresenceObserver,
        ObserverConfig,
        read_windows_private_ble_irks,
    )

    config = ObserverConfig.load(args.config)
    if config.observer_id != args.observer_id:
        raise RuntimeError("Wrong receiver; no Bluetooth changes made")
    receiver = SimpleNamespace(config=config)
    try:
        result = asyncio.run(
            reset_test_phone(
                args.anchor_address,
                args.container_id,
                args.config.parent / "fresh-test-targets.json",
                lambda: read_windows_private_ble_irks(strict=True),
                lambda targets: BlePresenceObserver._run_interactive_identity_removal(
                    receiver, args.request_id, targets
                ),
            )
        )
    except Exception as error:
        result = {"clean": False, "error": str(error)}
    result.update(
        request_id=args.request_id, host=platform.node(), observer_id=config.observer_id
    )
    args.result.write_text(json.dumps(result), encoding="utf-8")
    return 0 if result["clean"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
