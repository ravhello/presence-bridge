"""Bounded, transport-independent Linux enrollment engine (experimental)."""

from __future__ import annotations

import asyncio
import base64
import json
import os
import re
import tempfile
import time
from contextlib import suppress
from datetime import UTC, datetime
from pathlib import Path

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa

from ..protocol import (
    PairingLink,
    acceptance_proof,
    b64url_decode,
    completion_service_uuid,
    pairing_service_uuid,
    preflight_service_uuid,
    public_session_payload,
    verify_claim,
)
from . import VERSION
from .keys import BlueZKeys, ReceiverError, address

SESSION = "ef70387a-ba9d-4e83-9171-fea99252b57a"
CLAIM = "700dbb64-64ed-4f51-adae-b106a00e908a"
RESULT = "fb5312b1-c24c-42b5-8d21-5263874c258f"


def timestamp():
    return datetime.now(UTC).isoformat()


class LinuxReceiver:
    def __init__(
        self, transport, emit, state_path: Path, key_root=Path("/var/lib/bluetooth")
    ):
        self.transport = transport
        self.emit = emit
        self.state_path = state_path
        self.key_root = key_root
        self.observer_id = ""
        self.name = "Linux Bluetooth"
        self.task = None
        self.heartbeat = None
        self.session = None
        self.completed = asyncio.Event()
        self.result_sent = False
        self.peers = {}
        self.keys = None
        self.deadline = 0
        self.retired = set()
        self.lock = asyncio.Lock()

    async def start(self):
        try:
            await self.transport.connect()
            self.observer_id = (
                "linux_" + self.transport.adapter_address.replace(":", "").lower()
            )
            self.keys = BlueZKeys(self.key_root, self.transport.adapter_address)
            await asyncio.to_thread(self.keys.check_access)
            self.peers = await asyncio.to_thread(self._load)
            await self.transport.register_agent()
            await self.transport.scan()
            self.heartbeat = asyncio.create_task(self._heartbeat())
        except BaseException:
            await self.transport.close()
            raise

    def _load(self):
        if not self.state_path.exists():
            return {}
        if self.state_path.is_symlink() or self.state_path.stat().st_size > 65536:
            raise ReceiverError("Invalid receiver state file")
        data = json.loads(self.state_path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            raise ReceiverError("Invalid receiver state")
        return {
            key: address(value)
            for key, value in data.items()
            if re.fullmatch(r"[a-f0-9]{64}", key)
        }

    def _save(self):
        self.state_path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        # Unique 0600 temporary file; a previous crash cannot block future saves.
        fd, name = tempfile.mkstemp(prefix=".peers-", dir=self.state_path.parent)
        temporary = Path(name)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump(self.peers, handle)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, self.state_path)
        finally:
            with suppress(FileNotFoundError):
                temporary.unlink()

    async def _heartbeat(self):
        while True:
            alive = bool(
                self.transport.bus
                and self.transport.bus.connected
                and self.transport.owner
            )
            self.emit(
                "status",
                {
                    "name": self.name,
                    "online": alive,
                    "timestamp": timestamp(),
                    "version": VERSION,
                    "platform": "linux",
                    "capabilities": ["scanner", "app_pairing", "identity_removal"]
                    if alive
                    else [],
                },
            )
            if alive:
                self.emit(
                    "observations",
                    {
                        "timestamp": timestamp(),
                        "observations": self.transport.observations(),
                    },
                )
            await asyncio.sleep(5)

    def progress(self, state, code, message):
        self.emit(
            "pairing/status",
            {
                "session_id": self.session.session_id,
                "state": state,
                "detail_code": code,
                "message": message,
                "transport": "linux_bluez",
                **(
                    {
                        "attempt_expires_at": int(
                            time.time() + max(0, self.deadline - time.monotonic())
                        )
                    }
                    if self.deadline
                    else {}
                ),
            },
        )

    async def command(self, payload, retained=False):
        if retained or not isinstance(payload, dict):
            return
        async with self.lock:
            action = payload.get("action")
            if action == "start_app_pairing":
                link = PairingLink(
                    str(payload.get("session_id", "")),
                    str(payload.get("observer_id", "")),
                    int(payload.get("expires_at", 0)),
                    b64url_decode(str(payload.get("app_secret", ""))),
                )
                link.validate()
                if (
                    link.observer_id != self.observer_id
                    or link.session_id in self.retired
                ):
                    return
                if self.task and not self.task.done():
                    if self.session == link:
                        return  # MQTT QoS 1 redelivery is not a new attempt.
                    raise ReceiverError("Receiver already has an active pairing")
                public = serialization.load_der_public_key(
                    base64.b64decode(payload["public_key"], validate=True)
                )
                if not isinstance(public, rsa.RSAPublicKey) or public.key_size != 2048:
                    raise ReceiverError("Unsupported result encryption key")
                self.session, self.deadline, self.result_sent = link, 0, False
                self.completed.clear()
                self.task = asyncio.create_task(self._pair(link, public))
            elif (
                action == "cancel"
                and self.session
                and payload.get("session_id") == self.session.session_id
            ):
                await self._cancel()
            elif (
                action == "complete"
                and self.session
                and payload.get("session_id") == self.session.session_id
            ):
                if self.result_sent and time.monotonic() < self.deadline:
                    self.completed.set()
            elif action == "forget_identity":
                await self._forget(payload)

    async def _pair(self, link, public):
        path = None
        try:
            self.progress(
                "advertising",
                "receiver_ready",
                "Receiver ready; open Presence Pair and move nearby",
            )
            await self.transport.advertise(preflight_service_uuid(link))
            path = await self.transport.find_phone(
                pairing_service_uuid(link), link.expires_at
            )
            self.deadline = time.monotonic() + 300
            self.progress(
                "connecting",
                "receiver_proximity_confirmed",
                "Nearby iPhone detected; connecting",
            )
            async with asyncio.timeout_at(self.deadline):
                await self.transport.phone_connect(path)
                session = json.loads(await self.transport.read(path, SESSION))
                expected = public_session_payload(link)
                if any(session.get(k) != v for k, v in expected.items()):
                    raise ReceiverError("The iPhone is using a different QR code")
                claim = json.loads(await self.transport.read(path, CLAIM))
                if any(
                    claim.get(k) != v for k, v in expected.items()
                ) or not verify_claim(link, claim.get("proof", ""), allow_expired=True):
                    raise ReceiverError(
                        "The iPhone did not prove possession of this QR code"
                    )
                self.progress(
                    "bonding",
                    "iphone_claim_received",
                    "Accept the Bluetooth request on your iPhone if shown",
                )
                await self.transport.pair(path, self.deadline)
                peer = address(
                    self.transport.objects.get(path, {})
                    .get("org.bluez.Device1", {})
                    .get("Address", "")
                )
                bond = None
                for _ in range(20):
                    try:
                        bond = await asyncio.to_thread(self.keys.read, peer)
                        break
                    except ReceiverError:
                        await asyncio.sleep(0.5)
                if bond is None:
                    raise ReceiverError(
                        "BlueZ has no authenticated identity. Forget this receiver on the iPhone and retry; no other bonds were changed"
                    )
                ack = json.dumps(
                    {**expected, "status": "accepted", "proof": acceptance_proof(link)},
                    separators=(",", ":"),
                ).encode()
                await self.transport.write(path, RESULT, ack)
                self.progress(
                    "bonding",
                    "iphone_claim_accepted",
                    "Encrypted acknowledgement accepted; checking Home Assistant",
                )
                self.peers[bond.fingerprint] = peer
                await asyncio.to_thread(self._save)
                raw = json.dumps(
                    {
                        "irk": bond.irk,
                        "claim_verified": True,
                        "identity_address": peer,
                        "secure_exchange_complete": True,
                    },
                    separators=(",", ":"),
                ).encode()
                encrypted = public.encrypt(
                    raw,
                    padding.OAEP(
                        mgf=padding.MGF1(hashes.SHA256()),
                        algorithm=hashes.SHA256(),
                        label=None,
                    ),
                )
                self.result_sent = True
                self.emit(
                    "observations",
                    {
                        "timestamp": timestamp(),
                        "observations": self.transport.observations(),
                    },
                )
                self.progress(
                    "identity_captured",
                    "identity_captured",
                    "Home Assistant is verifying the live Bluetooth signal",
                )
                self.emit(
                    "pairing/result",
                    {
                        "schema": 2,
                        "session_id": link.session_id,
                        "ciphertext": base64.b64encode(encrypted).decode(),
                    },
                )
                await self.completed.wait()
                await self.transport.advertise(completion_service_uuid(link))
                await asyncio.sleep(min(20, max(0, self.deadline - time.monotonic())))
        except asyncio.CancelledError:
            self.progress("cancelled", "cancelled", "Pairing cancelled")
            raise
        except Exception as err:
            message = (
                str(err)
                if isinstance(err, ReceiverError)
                else "Pairing interrupted or timed out; retry near the receiver"
            )
            self.progress("error", "linux_pairing_failed", message)
        finally:
            self.retired.add(link.session_id)
            # Bound replay bookkeeping; invitations expire after ten minutes.
            if len(self.retired) > 256:
                self.retired = {link.session_id}
            self.transport.agent.target = None
            await self.transport.stop_advertising()
            if path:
                await self.transport.disconnect(path)

    async def _forget(self, payload):
        fingerprint = str(payload.get("fingerprint", ""))
        request = str(payload.get("request_id", ""))
        if (
            not re.fullmatch(r"[a-f0-9]{64}", fingerprint)
            or not re.fullmatch(r"[A-Za-z0-9_-]{16,96}", request)
            or payload.get("identity_id") != fingerprint[:16]
            or payload.get("observer_id") != self.observer_id
            or not time.time() < float(payload.get("expires_at", 0)) <= time.time() + 90
        ):
            return
        result = {k: payload[k] for k in ("request_id", "identity_id", "fingerprint")}
        result["success"] = False
        try:
            if self.task and not self.task.done():
                raise ReceiverError("Finish or cancel pairing before removing a device")
            peer = self.peers.get(fingerprint)
            if not peer:
                raise ReceiverError(
                    "No matching receiver identity record; no Bluetooth devices were removed"
                )
            try:
                bond = await asyncio.to_thread(self.keys.read, peer)
            except ReceiverError:
                # A missing/invalid key is not permission to delete a different bond.
                if any(
                    i.get("org.bluez.Device1", {}).get("Address") == peer
                    and i["org.bluez.Device1"].get("Paired")
                    for i in self.transport.objects.values()
                ):
                    raise
            else:
                if bond.fingerprint != fingerprint:
                    raise ReceiverError(
                        "Stored Bluetooth identity changed; removal stopped"
                    )
            await self.transport.remove(peer)
            # Keep the non-secret fingerprint mapping for idempotent redelivery.
            result["success"] = True
        except ReceiverError as err:
            result["message"] = str(err)
        except Exception:
            result["message"] = (
                "BlueZ did not confirm removal; HA association preserved"
            )
        self.emit("identity_removal/result", result)

    async def _cancel(self):
        if self.task and not self.task.done():
            self.task.cancel()
            with suppress(asyncio.CancelledError):
                await self.task

    async def close(self):
        await self._cancel()
        if self.heartbeat:
            self.heartbeat.cancel()
            with suppress(asyncio.CancelledError):
                await self.heartbeat
        await self.transport.close()
        self.emit(
            "status", {"online": False, "timestamp": timestamp(), "capabilities": []}
        )
