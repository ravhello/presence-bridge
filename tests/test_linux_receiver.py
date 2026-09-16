"""Real Linux backend and protocol, fake hardware. No device/certification claims."""

import asyncio
import base64
import importlib
import json
import sys
import time
import types
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa
from dbus_fast import Message, Variant
from dbus_fast.errors import DBusError

PACKAGE = "presence_bridge_portable"
namespace = types.ModuleType(PACKAGE)
namespace.__path__ = [
    str(Path(__file__).parents[1] / "custom_components/presence_bridge")
]
sys.modules[PACKAGE] = namespace
engine = importlib.import_module(f"{PACKAGE}.receiver.engine")
bluez = importlib.import_module(f"{PACKAGE}.receiver.bluez")
keys = importlib.import_module(f"{PACKAGE}.receiver.keys")
protocol = importlib.import_module(f"{PACKAGE}.protocol")
ADAPTER = "AA:BB:CC:DD:EE:FF"
PEER = "11:22:33:44:55:66"


def bond_file(tmp_path, auth=1, size=16):
    target = tmp_path / ADAPTER / PEER / "info"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        f"[IdentityResolvingKey]\nKey=000102030405060708090A0B0C0D0E0F\n[LongTermKey]\nKey={'11' * 16}\nAuthenticated={auth}\nEncSize={size}\n"
    )
    return target


@pytest.mark.skipif(
    sys.platform == "win32",
    reason="BlueZ uses POSIX filenames containing colons; exercised on Linux CI",
)
def test_keys_only_selected_peer_and_byte_order(tmp_path):
    bond_file(tmp_path)
    store = keys.BlueZKeys(tmp_path, ADAPTER)
    store.check_access()
    bond = store.read(PEER)
    assert bond.irk == "0F0E0D0C0B0A09080706050403020100"
    assert bond.irk not in repr(bond) and PEER not in repr(bond)
    for address in ("../info", "AA", "00:11:22:33:44:55/../"):
        with pytest.raises(keys.ReceiverError):
            store.read(address)


@pytest.mark.parametrize("auth,size", [(0, 16), (1, 7), (2, 16)])
@pytest.mark.skipif(
    sys.platform == "win32", reason="BlueZ POSIX storage is exercised on Linux CI"
)
def test_weak_keys_rejected(tmp_path, auth, size):
    bond_file(tmp_path, auth, size)
    with pytest.raises(keys.ReceiverError, match="authenticated"):
        keys.BlueZKeys(tmp_path, ADAPTER).read(PEER)


def test_dbus_agent_has_no_default_or_justworks_path():
    agent = bluez.Agent()
    agent.target, agent.deadline = "/phone", time.monotonic() + 10
    agent.RequestConfirmation("/phone", 123456)
    for name, args in (
        ("RequestConfirmation", ("/other", 123456)),
        ("RequestAuthorization", ("/phone",)),
        ("RequestPinCode", ("/phone",)),
        ("AuthorizeService", ("/phone", "uuid")),
    ):
        with pytest.raises(DBusError):
            getattr(agent, name)(*args)
    agent.deadline = 0
    with pytest.raises(DBusError):
        agent.RequestConfirmation("/phone", 123456)


def test_cached_rssi_is_not_a_new_observation():
    transport = bluez.BlueZ()
    transport.adapter = "/org/bluez/hci0"
    transport.owner = ":1.20"
    path = transport.adapter + "/dev_phone"
    transport.objects[path] = {
        bluez.DEVICE: {"Adapter": transport.adapter, "Address": PEER, "RSSI": -50}
    }
    assert transport.observations() == []
    message = Message.new_signal(
        path,
        "org.freedesktop.DBus.Properties",
        "PropertiesChanged",
        "sa{sv}as",
        [bluez.DEVICE, {"Connected": Variant("b", True)}, []],
    )
    message.sender = ":1.20"
    transport.signal(message)
    assert transport.observations() == []
    message.body[1] = {"RSSI": Variant("n", -52)}
    transport.signal(message)
    seen = transport.observations()[0]["seen_at"]
    assert transport.observations()[0]["seen_at"] == seen
    transport.seen[path] = time.time() - 181
    assert transport.observations() == []


@pytest.fixture(autouse=True)
def fake_engine_keys(monkeypatch):
    class FakeKeys:
        def __init__(self, *_):
            pass

        def check_access(self):
            pass

        def read(self, peer):
            return keys.Bond(peer, "0F0E0D0C0B0A09080706050403020100")

    monkeypatch.setattr(engine, "BlueZKeys", FakeKeys)


class FakeBlueZ:
    def __init__(self, link):
        self.adapter_address = ADAPTER
        self.bus = SimpleNamespace(connected=True)
        self.owner = ":1.20"
        self.agent = SimpleNamespace(target=None)
        self.objects = {"/phone": {bluez.DEVICE: {"Address": PEER, "Paired": True}}}
        self.connect = AsyncMock()
        self.close = AsyncMock()
        self.register_agent = AsyncMock()
        self.scan = AsyncMock()
        self.advertise = AsyncMock()
        self.stop_advertising = AsyncMock()
        self.find_phone = AsyncMock(return_value="/phone")
        self.phone_connect = AsyncMock()
        self.pair = AsyncMock()
        self.write = AsyncMock()
        self.disconnect = AsyncMock()
        self.remove = AsyncMock()
        self.read = AsyncMock(
            side_effect=[
                json.dumps(protocol.public_session_payload(link)).encode(),
                json.dumps(
                    {
                        **protocol.public_session_payload(link),
                        "proof": protocol.claim_proof(link),
                    }
                ).encode(),
            ]
        )
        self.observations = Mock(return_value=[])


def setup_receiver(tmp_path):
    link = protocol.PairingLink(
        "test_session_123456789",
        "linux_aabbccddeeff",
        int(time.time()) + 180,
        b"x" * 32,
    )
    transport = FakeBlueZ(link)
    events = []
    receiver = engine.LinuxReceiver(
        transport,
        lambda suffix, data: events.append((suffix, data)),
        tmp_path / "state/peers.json",
        tmp_path,
    )
    private = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    command = {
        "action": "start_app_pairing",
        "session_id": link.session_id,
        "observer_id": link.observer_id,
        "expires_at": link.expires_at,
        "app_secret": protocol.b64url_encode(link.secret),
        "public_key": base64.b64encode(
            private.public_key().public_bytes(
                serialization.Encoding.DER,
                serialization.PublicFormat.SubjectPublicKeyInfo,
            )
        ).decode(),
    }
    return receiver, transport, events, command, private


def test_pairing_requires_protected_ack_and_ha_commit(tmp_path):
    async def scenario():
        receiver, transport, events, command, private = setup_receiver(tmp_path)
        await receiver.start()
        try:
            await receiver.command(command, retained=True)
            assert receiver.task is None
            await receiver.command(command)
            original = receiver.task
            await receiver.command(command)
            assert receiver.task is original
            async with asyncio.timeout(3):
                while not any(s == "pairing/result" for s, _ in events):
                    await asyncio.sleep(0.01)
            transport.write.assert_awaited_once()
            result = next(p for s, p in events if s == "pairing/result")
            plaintext = private.decrypt(
                base64.b64decode(result["ciphertext"]),
                padding.OAEP(
                    mgf=padding.MGF1(hashes.SHA256()),
                    algorithm=hashes.SHA256(),
                    label=None,
                ),
            )
            assert json.loads(plaintext)["secure_exchange_complete"] is True
            assert "irk" not in result
            assert not receiver.completed.is_set()
            await receiver.command({"action": "complete", "session_id": "wrong"})
            assert not receiver.completed.is_set()
            await receiver.command(
                {"action": "complete", "session_id": command["session_id"]}
            )
            await asyncio.sleep(0.01)
            assert receiver.completed.is_set()
            assert transport.advertise.await_args.args[
                0
            ] == protocol.completion_service_uuid(receiver.session)
        finally:
            await receiver.close()

    asyncio.run(scenario())


@pytest.mark.parametrize("fault", ["claim", "write", "pair"])
def test_failure_never_exports_identity(tmp_path, fault):
    async def scenario():
        receiver, transport, events, command, _ = setup_receiver(tmp_path)
        if fault == "claim":
            transport.read.side_effect = [b"{}"]
        else:
            getattr(transport, fault).side_effect = keys.ReceiverError(
                "Expected failure"
            )
        await receiver.start()
        try:
            await receiver.command(command)
            await asyncio.wait_for(receiver.task, 3)
            assert not any(s == "pairing/result" for s, _ in events)
            if fault == "claim":
                transport.pair.assert_not_awaited()
            transport.remove.assert_not_awaited()
            assert transport.agent.target is None
        finally:
            await receiver.close()

    asyncio.run(scenario())


def test_removal_only_matching_fingerprint_and_replay(tmp_path):
    async def scenario():
        receiver, transport, events, _, _ = setup_receiver(tmp_path)
        await receiver.start()
        try:
            fingerprint = receiver.keys.read(PEER).fingerprint
            receiver.peers[fingerprint] = PEER
            request = {
                "action": "forget_identity",
                "request_id": "remove_request_123456",
                "observer_id": receiver.observer_id,
                "fingerprint": fingerprint,
                "identity_id": fingerprint[:16],
                "expires_at": time.time() + 70,
            }
            await receiver.command({**request, "identity_id": "wrong"})
            transport.remove.assert_not_awaited()
            await receiver.command(request)
            transport.remove.assert_awaited_once_with(PEER)
            assert events[-1][1]["success"]
            await receiver.command(request)
            assert events[-1][1]["success"]
        finally:
            await receiver.close()

    asyncio.run(scenario())
