"""BlueZ D-Bus transport; only owns its discovery reference and agent."""

import asyncio
import time
from contextlib import suppress
from datetime import UTC, datetime

from dbus_fast import BusType, Message, MessageType, Variant
from dbus_fast.constants import PropertyAccess
from dbus_fast.errors import DBusError
from dbus_fast.service import ServiceInterface, dbus_property, method

from .keys import ReceiverError, address

DEVICE = "org.bluez.Device1"
ADAPTER = "org.bluez.Adapter1"
CHARACTERISTIC = "org.bluez.GattCharacteristic1"
ADVERTISEMENT = "org.bluez.LEAdvertisement1"
ADV_MANAGER = "org.bluez.LEAdvertisingManager1"
AGENT_PATH = "/org/presencebridge/agent"
ADV_PATH = "/org/presencebridge/advertisement"


class Agent(ServiceInterface):
    """Never register as the system default or accept an unrelated request."""

    def __init__(self):
        super().__init__("org.bluez.Agent1")
        self.target = None
        self.deadline = 0

    @method()
    def Release(self):
        self.target = None

    @method()
    def Cancel(self):
        self.target = None

    @method()
    def RequestConfirmation(self, device: "o", passkey: "u"):
        if device != self.target or time.monotonic() >= self.deadline:
            raise DBusError(
                "org.bluez.Error.Rejected", "No matching QR-authorized session"
            )

    @method()
    def RequestAuthorization(self, device: "o"):
        raise DBusError(
            "org.bluez.Error.Rejected", "Unauthenticated pairing is not allowed"
        )

    @method()
    def AuthorizeService(self, device: "o", uuid: "s"):
        raise DBusError(
            "org.bluez.Error.Rejected", "Profile authorization is not required"
        )

    @method()
    def RequestPinCode(self, device: "o") -> "s":
        raise DBusError(
            "org.bluez.Error.Rejected", "Legacy PIN pairing is not supported"
        )

    @method()
    def RequestPasskey(self, device: "o") -> "u":
        raise DBusError("org.bluez.Error.Rejected", "Passkey entry is not supported")


class Advertisement(ServiceInterface):
    def __init__(self, uuid):
        super().__init__(ADVERTISEMENT)
        self.uuid = uuid

    @dbus_property(access=PropertyAccess.READ)
    def Type(self) -> "s":
        return "broadcast"

    @dbus_property(access=PropertyAccess.READ)
    def ServiceUUIDs(self) -> "as":
        return [self.uuid]

    @method()
    def Release(self):
        pass


def values(props):
    return {key: value.value for key, value in props.items()}


class BlueZ:
    def __init__(self, adapter_address=""):
        self.requested = address(adapter_address) if adapter_address else ""
        self.bus = None
        self.adapter = None
        self.adapter_address = None
        self.agent = Agent()
        self.objects = {}
        self.seen = {}
        self.discovering = False
        self.advertising = False
        self.agent_registered = False
        self.changed = asyncio.Event()
        self.owner = None

    async def call(self, path, interface, member, signature="", body=None, timeout=15):
        response = await asyncio.wait_for(
            self.bus.call(
                Message(
                    destination="org.bluez",
                    path=path,
                    interface=interface,
                    member=member,
                    signature=signature,
                    body=body or [],
                )
            ),
            timeout,
        )
        if response.message_type == MessageType.ERROR:
            # BlueZ error bodies can contain device addresses. Do not expose them.
            raise ReceiverError(f"BlueZ: {response.error_name}")
        return response.body

    async def connect(self):
        from dbus_fast.aio import MessageBus  # noqa: PLC0415

        try:
            self.bus = await asyncio.wait_for(
                MessageBus(bus_type=BusType.SYSTEM).connect(), 10
            )
            owner = await asyncio.wait_for(
                self.bus.call(
                    Message(
                        destination="org.freedesktop.DBus",
                        path="/org/freedesktop/DBus",
                        interface="org.freedesktop.DBus",
                        member="GetNameOwner",
                        signature="s",
                        body=["org.bluez"],
                    )
                ),
                10,
            )
            if owner.message_type == MessageType.ERROR:
                raise ReceiverError("BlueZ is not running")
            self.owner = owner.body[0]
            objects = (
                await self.call(
                    "/", "org.freedesktop.DBus.ObjectManager", "GetManagedObjects"
                )
            )[0]
            self.objects = {
                path: {i: values(p) for i, p in interfaces.items()}
                for path, interfaces in objects.items()
            }
            adapters = [
                (path, i[ADAPTER])
                for path, i in self.objects.items()
                if ADAPTER in i
                and (not self.requested or i[ADAPTER].get("Address") == self.requested)
            ]
            if len(adapters) != 1:
                raise ReceiverError(
                    "Select one local Bluetooth adapter by its MAC address in integration options"
                )
            self.adapter, props = adapters[0]
            self.adapter_address = address(props["Address"])
            if not props.get("Powered"):
                raise ReceiverError(
                    "Selected Bluetooth adapter is off; enable it in the host operating system"
                )
            if ADV_MANAGER not in self.objects[self.adapter]:
                raise ReceiverError(
                    "Adapter lacks BLE advertising; use a compatible local USB adapter or remote receiver"
                )
            # Match signals from BlueZ only, and let dbus-daemon filter their sender.
            for rule in (
                "type='signal',sender='org.bluez',interface='org.freedesktop.DBus.Properties'",
                "type='signal',sender='org.bluez',interface='org.freedesktop.DBus.ObjectManager'",
                "type='signal',sender='org.freedesktop.DBus',interface='org.freedesktop.DBus',member='NameOwnerChanged',arg0='org.bluez'",
            ):
                response = await asyncio.wait_for(
                    self.bus.call(
                        Message(
                            destination="org.freedesktop.DBus",
                            path="/org/freedesktop/DBus",
                            interface="org.freedesktop.DBus",
                            member="AddMatch",
                            signature="s",
                            body=[rule],
                        )
                    ),
                    10,
                )
                if response.message_type == MessageType.ERROR:
                    raise ReceiverError("D-Bus signal subscription denied")
            self.bus.add_message_handler(self.signal)
        except BaseException:
            await self.close()
            raise

    def signal(self, message):
        if message.message_type != MessageType.SIGNAL:
            return
        if (
            message.sender == "org.freedesktop.DBus"
            and message.member == "NameOwnerChanged"
            and message.body[0] == "org.bluez"
        ):
            self.owner = None
            self.objects.clear()
            self.seen.clear()
            self.agent.target = None
            self.changed.set()
            return
        if not self.owner or message.sender != self.owner:
            return
        self.changed.set()
        if message.interface == "org.freedesktop.DBus.ObjectManager":
            if message.member == "InterfacesAdded":
                path, interfaces = message.body
                self.objects.setdefault(path, {}).update(
                    {i: values(p) for i, p in interfaces.items()}
                )
                if DEVICE in interfaces and "RSSI" in interfaces[DEVICE]:
                    self.seen[path] = time.time()
            elif message.member == "InterfacesRemoved":
                path, interfaces = message.body
                for interface in interfaces:
                    self.objects.get(path, {}).pop(interface, None)
                if DEVICE in interfaces:
                    self.seen.pop(path, None)
        elif (
            message.interface == "org.freedesktop.DBus.Properties"
            and message.member == "PropertiesChanged"
        ):
            interface, changed, invalidated = message.body
            props = self.objects.setdefault(message.path, {}).setdefault(interface, {})
            props.update(values(changed))
            for key in invalidated:
                props.pop(key, None)
            if interface == DEVICE and set(changed) & {
                "RSSI",
                "ManufacturerData",
                "ServiceData",
            }:
                self.seen[message.path] = time.time()

    def observations(self):
        rows = []
        for path, seen in sorted(
            self.seen.items(), key=lambda row: row[1], reverse=True
        ):
            props = self.objects.get(path, {}).get(DEVICE, {})
            if (
                props.get("Adapter") != self.adapter
                or not 0 <= time.time() - seen <= 180
            ):
                continue
            if "RSSI" in props and "Address" in props:
                rows.append(
                    {
                        "address": props["Address"],
                        "rssi": props["RSSI"],
                        "seen_at": datetime.fromtimestamp(seen, UTC).isoformat(),
                    }
                )
        return rows[:200]

    async def scan(self):
        await self.call(
            self.adapter,
            ADAPTER,
            "SetDiscoveryFilter",
            "a{sv}",
            [{"Transport": Variant("s", "le"), "DuplicateData": Variant("b", True)}],
        )
        await self.call(self.adapter, ADAPTER, "StartDiscovery")
        self.discovering = True

    async def advertise(self, uuid):
        await self.stop_advertising()
        self.bus.export(ADV_PATH, Advertisement(uuid))
        try:
            await self.call(
                self.adapter,
                ADV_MANAGER,
                "RegisterAdvertisement",
                "oa{sv}",
                [ADV_PATH, {}],
            )
            self.advertising = True
        except BaseException:
            self.bus.unexport(ADV_PATH)
            raise

    async def stop_advertising(self):
        if self.advertising:
            with suppress(ReceiverError, TimeoutError):
                await self.call(
                    self.adapter,
                    ADV_MANAGER,
                    "UnregisterAdvertisement",
                    "o",
                    [ADV_PATH],
                )
            self.bus.unexport(ADV_PATH)
            self.advertising = False

    async def register_agent(self):
        if not self.agent_registered:
            self.bus.export(AGENT_PATH, self.agent)
            try:
                await self.call(
                    "/org/bluez",
                    "org.bluez.AgentManager1",
                    "RegisterAgent",
                    "os",
                    [AGENT_PATH, "DisplayYesNo"],
                )
                self.agent_registered = True
            except BaseException:
                self.bus.unexport(AGENT_PATH)
                raise

    async def find_phone(self, uuid, expiry):
        while time.time() < expiry:
            for path, interfaces in list(self.objects.items()):
                props = interfaces.get(DEVICE, {})
                if (
                    props.get("Adapter") == self.adapter
                    and uuid in props.get("UUIDs", [])
                    and props.get("RSSI", -127) >= -75
                    and 0 <= time.time() - self.seen.get(path, 0) < 10
                ):
                    return path
            await asyncio.sleep(0.3)
        raise ReceiverError("QR expired before a nearby iPhone was detected")

    async def phone_connect(self, path):
        if not self.objects.get(path, {}).get(DEVICE, {}).get("Connected"):
            await self.call(path, DEVICE, "Connect", timeout=20)
        async with asyncio.timeout(20):
            while (
                not self.objects.get(path, {}).get(DEVICE, {}).get("ServicesResolved")
            ):
                self.changed.clear()
                await self.changed.wait()

    def characteristic(self, path, uuid):
        matches = [
            p
            for p, i in self.objects.items()
            if p.startswith(path + "/")
            and i.get(CHARACTERISTIC, {}).get("UUID", "").lower() == uuid.lower()
        ]
        if len(matches) != 1:
            raise ReceiverError(
                "Current iPhone GATT characteristic is missing or ambiguous"
            )
        return matches[0]

    async def read(self, path, uuid):
        data = (
            await self.call(
                self.characteristic(path, uuid),
                CHARACTERISTIC,
                "ReadValue",
                "a{sv}",
                [{}],
            )
        )[0]
        if len(data) > 2048:
            raise ReceiverError("Phone payload exceeds the protocol limit")
        return bytes(data)

    async def write(self, path, uuid, data):
        await self.call(
            self.characteristic(path, uuid),
            CHARACTERISTIC,
            "WriteValue",
            "aya{sv}",
            [data, {"type": Variant("s", "request")}],
        )

    async def pair(self, path, deadline):
        self.agent.target, self.agent.deadline = path, deadline
        try:
            if not self.objects.get(path, {}).get(DEVICE, {}).get("Paired"):
                await self.call(
                    path,
                    DEVICE,
                    "Pair",
                    timeout=max(1, min(90, deadline - time.monotonic())),
                )
        except BaseException:
            with suppress(ReceiverError, TimeoutError):
                await self.call(path, DEVICE, "CancelPairing", timeout=5)
            raise
        finally:
            self.agent.target = None

    async def disconnect(self, path):
        with suppress(ReceiverError, TimeoutError):
            await self.call(path, DEVICE, "Disconnect")

    async def remove(self, peer):
        peer = address(peer)
        targets = [
            p
            for p, i in self.objects.items()
            if i.get(DEVICE, {}).get("Adapter") == self.adapter
            and i.get(DEVICE, {}).get("Address") == peer
        ]
        for target in targets:
            await self.call(self.adapter, ADAPTER, "RemoveDevice", "o", [target])
        # Removal is confirmed by BlueZ, not by a local JSON edit.
        async with asyncio.timeout(10):
            while any(
                self.objects.get(p, {}).get(DEVICE, {}).get("Paired") for p in targets
            ):
                self.changed.clear()
                await self.changed.wait()

    async def close(self):
        if not self.bus:
            return
        self.agent.target = None
        await self.stop_advertising()
        if self.discovering:
            with suppress(ReceiverError, TimeoutError):
                await self.call(self.adapter, ADAPTER, "StopDiscovery")
            self.discovering = False
        if self.agent_registered:
            with suppress(ReceiverError, TimeoutError):
                await self.call(
                    "/org/bluez",
                    "org.bluez.AgentManager1",
                    "UnregisterAgent",
                    "o",
                    [AGENT_PATH],
                )
            self.agent_registered = False
        self.bus.disconnect()
        self.bus = None
