from __future__ import annotations

import json
import time
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

from bleak import BleakScanner
from bleak.exc import (
    BleakCharacteristicNotFoundError,
    BleakError,
    BleakGATTProtocolError,
)
from protocol import (
    PairingLink,
    acceptance_proof,
    claim_proof,
    pairing_service_uuid,
)
from reverse_gatt_client import (
    BondResetRequiredError,
    ReverseGattError,
    ReverseGattPairingClient,
    ReverseGattResult,
)


class ReverseGattPairingClientTest(unittest.IsolatedAsyncioTestCase):
    def test_matches_service_uuid_from_advertisement_or_service_data(self) -> None:
        client = ReverseGattPairingClient(
            service_uuid="service",
            session_uuid="session",
            claim_uuid="claim",
            result_uuid="result",
        )
        device = SimpleNamespace(name=None)

        self.assertTrue(
            client._matches_advertisement(
                device,
                SimpleNamespace(
                    service_uuids=["SERVICE"],
                    service_data={},
                    local_name=None,
                ),
            )
        )
        self.assertTrue(
            client._matches_advertisement(
                device,
                SimpleNamespace(
                    service_uuids=[],
                    service_data={"SERVICE": b""},
                    local_name=None,
                ),
            )
        )

    def test_matches_session_specific_service_without_dropping_legacy_app(self) -> None:
        link = PairingLink(
            session_id="abcdefghijklmnopQRSTUVWX",
            observer_id="dell_cucina",
            expires_at=int(time.time()) + 180,
            secret=bytes(range(32)),
        )
        client = ReverseGattPairingClient(
            service_uuid="61dd168c-4ec1-40de-a78c-ccdce5774bba",
            session_uuid="session",
            claim_uuid="claim",
            result_uuid="result",
        )
        dynamic_uuid = pairing_service_uuid(link)
        client._session_service_uuid = dynamic_uuid
        client.service_uuids.add(dynamic_uuid)

        self.assertTrue(
            client._matches_advertisement(
                SimpleNamespace(name=None),
                SimpleNamespace(
                    service_uuids=[dynamic_uuid.upper()],
                    service_data={},
                    local_name=None,
                ),
            )
        )
        self.assertEqual(client._matched_service_uuid, dynamic_uuid)
        client._matched_address = "40:01:02:0A:C4:A6"
        self.assertEqual(
            client.lease_payload,
            {
                "matched_address": "40:01:02:0A:C4:A6",
                "session_scoped_advertisement": True,
            },
        )

        self.assertTrue(
            client._matches_advertisement(
                SimpleNamespace(name=None),
                SimpleNamespace(
                    service_uuids=[client.service_uuid],
                    service_data={},
                    local_name=None,
                ),
            )
        )

    def test_rejects_presence_pair_name_without_matching_uuid(self) -> None:
        client = ReverseGattPairingClient(
            service_uuid="service",
            session_uuid="session",
            claim_uuid="claim",
            result_uuid="result",
        )
        advertisement = SimpleNamespace(
            service_uuids=[],
            service_data={},
            local_name="Presence Pair",
        )

        self.assertFalse(
            client._matches_advertisement(
                SimpleNamespace(name=None),
                advertisement,
            )
        )
        self.assertFalse(
            client._matches_advertisement(
                SimpleNamespace(name="Unrelated device"),
                SimpleNamespace(
                    service_uuids=[],
                    service_data={},
                    local_name=None,
                ),
            )
        )

    def test_rejects_truncated_ios_name_without_matching_uuid(self) -> None:
        client = ReverseGattPairingClient(
            service_uuid="service",
            session_uuid="session",
            claim_uuid="claim",
            result_uuid="result",
        )

        self.assertFalse(
            client._matches_advertisement(
                SimpleNamespace(name=None),
                SimpleNamespace(
                    service_uuids=[],
                    service_data={},
                    local_name="Presence",
                ),
            )
        )

    async def test_connects_pairs_verifies_and_acknowledges(self) -> None:
        link = PairingLink(
            session_id="abcdefghijklmnopQRSTUVWX",
            observer_id="dell_cucina",
            expires_at=int(time.time()) + 180,
            secret=bytes(range(32)),
        )
        session = {
            "v": link.version,
            "sid": link.session_id,
            "oid": link.observer_id,
            "exp": link.expires_at,
        }
        claim = {**session, "proof": claim_proof(link)}
        device = SimpleNamespace(address="40:01:02:0A:C4:A6", name="Presence Pair")
        initial_client = Mock()
        initial_client.disconnect = AsyncMock()
        initial_client.unpair = AsyncMock()
        initial_client.is_connected = True
        events: list[str] = []

        async def read_characteristic(uuid: str) -> bytes:
            events.append(f"read:{uuid}")
            return json.dumps(session if uuid == "session" else claim).encode()

        async def pair() -> None:
            events.append("pair")

        async def write_characteristic(
            uuid: str,
            _value: bytes,
            *,
            response: bool,
        ) -> None:
            self.assertTrue(response)
            events.append(f"write:{uuid}")

        initial_client.read_gatt_char = AsyncMock(side_effect=read_characteristic)
        initial_client.pair = AsyncMock(side_effect=pair)
        initial_client.write_gatt_char = AsyncMock(side_effect=write_characteristic)
        progress: list[str] = []
        client = ReverseGattPairingClient(
            service_uuid="service",
            session_uuid="session",
            claim_uuid="claim",
            result_uuid="result",
            progress_callback=lambda code, _message: progress.append(code),
        )

        with (
            patch.object(
                client,
                "_open_candidate",
                return_value=initial_client,
            ),
            patch("reverse_gatt_client.asyncio.sleep", new=AsyncMock()),
        ):
            result = await client._pair_candidate(
                device,
                link,
                40,
                allow_bond_reset=True,
            )

        self.assertEqual(result.address, device.address)
        initial_client.pair.assert_not_awaited()
        initial_client.disconnect.assert_awaited_once()
        acknowledgement = json.loads(
            initial_client.write_gatt_char.await_args.args[1].decode()
        )
        self.assertEqual(acknowledgement["status"], "accepted")
        self.assertEqual(acknowledgement["proof"], acceptance_proof(link))
        self.assertEqual(
            events,
            ["read:session", "read:claim", "write:result"],
        )
        self.assertEqual(progress[-1], "iphone_claim_accepted")

    async def test_closed_ack_channel_after_bond_reconnects_without_unpairing(self) -> None:
        link = PairingLink(
            session_id="abcdefghijklmnopQRSTUVWX",
            observer_id="dell_cucina",
            expires_at=int(time.time()) + 180,
            secret=bytes(range(32)),
        )
        session = {
            "v": link.version,
            "sid": link.session_id,
            "oid": link.observer_id,
            "exp": link.expires_at,
        }
        claim = {**session, "proof": claim_proof(link)}
        device = SimpleNamespace(address="40:01:02:0A:C4:A6", name="Presence Pair")
        bleak_client = Mock(
            is_connected=True,
            read_gatt_char=AsyncMock(
                side_effect=[
                    json.dumps(session).encode(),
                    json.dumps(claim).encode(),
                ]
            ),
            pair=AsyncMock(),
            unpair=AsyncMock(),
            write_gatt_char=AsyncMock(
                side_effect=[BleakGATTProtocolError(0x05), TimeoutError()]
            ),
            disconnect=AsyncMock(),
        )
        progress: list[str] = []
        client = ReverseGattPairingClient(
            service_uuid="service",
            session_uuid="session",
            claim_uuid="claim",
            result_uuid="result",
            progress_callback=lambda code, _message: progress.append(code),
        )

        with (
            patch.object(client, "_open_candidate", return_value=bleak_client),
            patch("reverse_gatt_client.asyncio.sleep", new=AsyncMock()),
            self.assertRaises(ReverseGattError) as raised,
        ):
            await client._pair_candidate(
                device,
                link,
                60,
                allow_bond_reset=True,
            )

        self.assertEqual(raised.exception.detail_code, "iphone_bond_reconnecting")
        bleak_client.pair.assert_awaited_once()
        bleak_client.unpair.assert_not_awaited()
        self.assertEqual(bleak_client.write_gatt_char.await_count, 2)
        self.assertEqual(progress[-1], "iphone_bond_reconnecting")

    async def test_winrt_pair_error_is_accepted_when_bond_was_committed(self) -> None:
        link = PairingLink(
            session_id="abcdefghijklmnopQRSTUVWX",
            observer_id="dell_cucina",
            expires_at=int(time.time()) + 180,
            secret=bytes(range(32)),
        )
        session = {
            "v": link.version,
            "sid": link.session_id,
            "oid": link.observer_id,
            "exp": link.expires_at,
        }
        claim = {**session, "proof": claim_proof(link)}
        device = SimpleNamespace(address="40:01:02:0A:C4:A6", name="Presence Pair")
        bleak_client = Mock(
            is_connected=True,
            read_gatt_char=AsyncMock(
                side_effect=[json.dumps(session).encode(), json.dumps(claim).encode()]
            ),
            pair=AsyncMock(
                side_effect=BleakError("Failure trying to pair with device!")
            ),
            unpair=AsyncMock(),
            write_gatt_char=AsyncMock(side_effect=BleakGATTProtocolError(0x05)),
            disconnect=AsyncMock(),
        )
        client = ReverseGattPairingClient(
            service_uuid="service",
            session_uuid="session",
            claim_uuid="claim",
            result_uuid="result",
        )

        with (
            patch.object(client, "_open_candidate", return_value=bleak_client),
            patch.object(
                client,
                "_windows_reports_paired",
                new=AsyncMock(return_value=True),
            ),
            patch("reverse_gatt_client.asyncio.sleep", new=AsyncMock()),
            self.assertRaises(ReverseGattError) as raised,
        ):
            await client._pair_candidate(
                device,
                link,
                40,
                allow_bond_reset=True,
            )

        self.assertEqual(raised.exception.detail_code, "iphone_bond_reconnecting")
        bleak_client.unpair.assert_not_awaited()
        bleak_client.write_gatt_char.assert_awaited_once()

    def test_uses_random_winrt_address_for_ios_advertisement(self) -> None:
        address_type = SimpleNamespace(name="RANDOM")
        device = SimpleNamespace(
            details=SimpleNamespace(
                adv=SimpleNamespace(bluetooth_address_type=address_type),
                scan=None,
            )
        )
        self.assertEqual(
            ReverseGattPairingClient._windows_address_type(device),
            "random",
        )

    async def test_connects_with_native_random_winrt_address(self) -> None:
        device = SimpleNamespace(address="40:01:02:0A:C4:A6")
        bleak_client = Mock()
        bleak_client.connect = AsyncMock()
        bleak_client.is_connected = True
        client = ReverseGattPairingClient(
            service_uuid="service",
            session_uuid="session",
            claim_uuid="claim",
            result_uuid="result",
        )

        with patch(
            "reverse_gatt_client.BleakClient",
            return_value=bleak_client,
        ) as constructor:
            connected = await client._connect_candidate(
                device,
                connection_timeout=12,
                address_type="random",
                filter_services=True,
                use_cached_services=None,
                pair_before_discovery=True,
            )

        self.assertIs(connected, bleak_client)
        self.assertEqual(
            constructor.call_args.kwargs["winrt"],
            {"address_type": "random"},
        )
        self.assertEqual(constructor.call_args.kwargs["services"], ["service"])
        self.assertTrue(constructor.call_args.kwargs["pair"])

    async def test_enables_winrt_service_change_retry_before_connect(self) -> None:
        device = SimpleNamespace(address="40:01:02:0A:C4:A6")
        backend = SimpleNamespace(_retry_on_services_changed=False)
        bleak_client = Mock(_backend=backend)
        bleak_client.connect = AsyncMock()
        bleak_client.is_connected = True
        client = ReverseGattPairingClient(
            service_uuid="service",
            session_uuid="session",
            claim_uuid="claim",
            result_uuid="result",
        )

        with (
            patch("reverse_gatt_client.sys.platform", "win32"),
            patch("reverse_gatt_client.BleakClient", return_value=bleak_client),
        ):
            await client._connect_candidate(
                device,
                connection_timeout=12,
                address_type="random",
                filter_services=True,
                use_cached_services=None,
                pair_before_discovery=False,
            )

        self.assertTrue(backend._retry_on_services_changed)
        bleak_client.connect.assert_awaited_once()

    async def test_open_candidate_fallbacks_do_not_pair_before_qr_verification(
        self,
    ) -> None:
        device = SimpleNamespace(address="40:01:02:0A:C4:A6")
        connected = Mock(
            services=[
                SimpleNamespace(
                    uuid="service",
                    characteristics=[SimpleNamespace(uuid="session")],
                )
            ]
        )
        client = ReverseGattPairingClient(
            service_uuid="service",
            session_uuid="session",
            claim_uuid="claim",
            result_uuid="result",
        )

        with patch.object(
            client,
            "_connect_candidate",
            new=AsyncMock(side_effect=[TimeoutError(), connected]),
        ) as connect:
            result = await client._open_candidate(
                device,
                60,
            )

        self.assertIs(result, connected)
        self.assertFalse(connect.await_args_list[0].kwargs["pair_before_discovery"])
        self.assertFalse(connect.await_args_list[1].kwargs["pair_before_discovery"])
        self.assertIsNone(connect.await_args_list[0].kwargs["use_cached_services"])
        self.assertFalse(connect.await_args_list[1].kwargs["use_cached_services"])
        self.assertFalse(connect.await_args_list[1].kwargs["filter_services"])

    async def test_session_scoped_service_does_not_pair_before_discovery(self) -> None:
        device = SimpleNamespace(address="40:01:02:0A:C4:A6")
        connected = Mock(
            services=[
                SimpleNamespace(
                    uuid="session-service",
                    characteristics=[SimpleNamespace(uuid="session")],
                )
            ]
        )
        client = ReverseGattPairingClient(
            service_uuid="legacy-service",
            session_uuid="session",
            claim_uuid="claim",
            result_uuid="result",
        )
        client._session_service_uuid = "session-service"
        client._matched_service_uuid = "session-service"

        with patch.object(
            client,
            "_connect_candidate",
            new=AsyncMock(return_value=connected),
        ) as connect:
            result = await client._open_candidate(device, 60)

        self.assertIs(result, connected)
        self.assertFalse(connect.await_args.kwargs["pair_before_discovery"])
        self.assertIsNone(connect.await_args.kwargs["use_cached_services"])
        self.assertTrue(connect.await_args.kwargs["filter_services"])

    async def test_confirmed_bond_pairs_before_encrypted_service_discovery(self) -> None:
        device = SimpleNamespace(address="40:01:02:0A:C4:A6")
        connected = Mock(
            services=[
                SimpleNamespace(
                    uuid="session-service",
                    characteristics=[SimpleNamespace(uuid="session")],
                )
            ]
        )
        client = ReverseGattPairingClient(
            service_uuid="legacy-service",
            session_uuid="session",
            claim_uuid="claim",
            result_uuid="result",
        )
        client._session_service_uuid = "session-service"
        client._matched_service_uuid = "session-service"
        client._secure_bond_confirmed = True

        with patch.object(
            client,
            "_connect_candidate",
            new=AsyncMock(return_value=connected),
        ) as connect:
            result = await client._open_candidate(device, 60)

        self.assertIs(result, connected)
        self.assertTrue(connect.await_args.kwargs["pair_before_discovery"])
        self.assertFalse(connect.await_args.kwargs["use_cached_services"])
        self.assertTrue(connect.await_args.kwargs["filter_services"])

    async def test_session_scoped_advertisement_refreshes_saved_bond_once(self) -> None:
        link = PairingLink(
            session_id="abcdefghijklmnopQRSTUVWX",
            observer_id="dell_cucina",
            expires_at=int(time.time()) + 180,
            secret=bytes(range(32)),
        )
        dynamic_uuid = pairing_service_uuid(link)
        address_type = SimpleNamespace(name="RANDOM")
        device = SimpleNamespace(
            address="40:01:02:0A:C4:A6",
            name="Presence Pair",
            details=SimpleNamespace(
                adv=SimpleNamespace(bluetooth_address_type=address_type),
                scan=None,
            ),
        )
        advertisement = SimpleNamespace(
            service_uuids=[dynamic_uuid],
            service_data={},
            local_name="Presence Pair",
            rssi=-52,
            tx_power=None,
        )
        expected = ReverseGattResult(address=device.address, name=device.name)
        client = ReverseGattPairingClient(
            service_uuid="service",
            session_uuid="session",
            claim_uuid="claim",
            result_uuid="result",
        )

        async def find_device(filter_func: object, **_kwargs: object) -> object:
            self.assertTrue(filter_func(device, advertisement))
            return device

        with (
            patch.object(
                BleakScanner,
                "find_device_by_filter",
                new=AsyncMock(side_effect=find_device),
            ),
            patch.object(client, "_unpair_candidate", new=AsyncMock()) as unpair,
            patch.object(
                client,
                "_pair_candidate",
                new=AsyncMock(
                    side_effect=[
                        BleakCharacteristicNotFoundError("session"),
                        expected,
                    ]
                ),
            ) as pair_candidate,
            patch("reverse_gatt_client.asyncio.sleep", new=AsyncMock()),
        ):
            actual = await client.async_pair(link, 30)

        self.assertEqual(actual, expected)
        unpair.assert_awaited_once_with(device, address_type="random")
        self.assertEqual(pair_candidate.await_count, 2)
        self.assertTrue(pair_candidate.await_args_list[0].kwargs["allow_bond_reset"])
        self.assertFalse(pair_candidate.await_args_list[1].kwargs["allow_bond_reset"])

    async def test_weak_signal_waits_before_opening_gatt(self) -> None:
        link = PairingLink(
            session_id="abcdefghijklmnopQRSTUVWX",
            observer_id="dell_cucina",
            expires_at=int(time.time()) + 180,
            secret=bytes(range(32)),
        )
        dynamic_uuid = pairing_service_uuid(link)
        device = SimpleNamespace(address="40:01:02:0A:C4:A6", name="Presence Pair")
        weak = SimpleNamespace(
            service_uuids=[dynamic_uuid],
            service_data={},
            local_name="Presence Pair",
            rssi=-89,
            tx_power=None,
        )
        strong = SimpleNamespace(
            service_uuids=[dynamic_uuid],
            service_data={},
            local_name="Presence Pair",
            rssi=-60,
            tx_power=None,
        )
        expected = ReverseGattResult(address=device.address, name=device.name)
        progress: list[str] = []
        client = ReverseGattPairingClient(
            service_uuid="service",
            session_uuid="session",
            claim_uuid="claim",
            result_uuid="result",
            progress_callback=lambda detail, _message: progress.append(detail),
        )
        advertisements = iter((weak, strong))

        async def find_device(filter_func: object, **_kwargs: object) -> object:
            self.assertTrue(filter_func(device, next(advertisements)))
            return device

        with (
            patch.object(
                BleakScanner,
                "find_device_by_filter",
                new=AsyncMock(side_effect=find_device),
            ),
            patch.object(
                client,
                "_pair_candidate",
                new=AsyncMock(return_value=expected),
            ) as pair_candidate,
            patch("reverse_gatt_client.asyncio.sleep", new=AsyncMock()),
        ):
            actual = await client.async_pair(link, 30)

        self.assertEqual(actual, expected)
        pair_candidate.assert_awaited_once()
        self.assertIn("iphone_signal_too_weak", progress)
        self.assertEqual(client.lease_payload["rssi"], -60)

    async def test_open_candidate_rejects_empty_cache_and_uses_fresh_discovery(
        self,
    ) -> None:
        device = SimpleNamespace(address="40:01:02:0A:C4:A6")
        empty_native = Mock(
            is_connected=True,
            services=[],
            disconnect=AsyncMock(),
        )
        fresh = Mock(
            services=[
                SimpleNamespace(
                    uuid="service",
                    characteristics=[SimpleNamespace(uuid="session")],
                )
            ]
        )
        client = ReverseGattPairingClient(
            service_uuid="service",
            session_uuid="session",
            claim_uuid="claim",
            result_uuid="result",
        )

        with patch.object(
            client,
            "_connect_candidate",
            new=AsyncMock(side_effect=[empty_native, fresh]),
        ) as connect:
            result = await client._open_candidate(
                device,
                60,
            )

        self.assertIs(result, fresh)
        self.assertEqual(connect.await_count, 2)
        self.assertFalse(connect.await_args_list[1].kwargs["use_cached_services"])
        self.assertFalse(connect.await_args_list[1].kwargs["filter_services"])
        self.assertFalse(connect.await_args_list[0].kwargs["pair_before_discovery"])
        self.assertFalse(connect.await_args_list[1].kwargs["pair_before_discovery"])
        empty_native.disconnect.assert_awaited_once()

    async def test_timeout_never_clears_bond_before_qr_verification(self) -> None:
        device = SimpleNamespace(address="40:01:02:0A:C4:A6")
        client = ReverseGattPairingClient(
            service_uuid="service",
            session_uuid="session",
            claim_uuid="claim",
            result_uuid="result",
        )

        with (
            patch.object(
                client,
                "_connect_candidate",
                new=AsyncMock(side_effect=TimeoutError()),
            ),
            patch.object(client, "_unpair_candidate", new=AsyncMock()) as unpair,
            patch("reverse_gatt_client.time.monotonic", side_effect=range(100, 400)),
            self.assertRaises(TimeoutError),
        ):
            await client._open_candidate(
                device,
                82,
            )

        unpair.assert_not_awaited()

    async def test_gatt_error_never_clears_bond_before_qr_verification(self) -> None:
        device = SimpleNamespace(address="40:01:02:0A:C4:A6")
        client = ReverseGattPairingClient(
            service_uuid="service",
            session_uuid="session",
            claim_uuid="claim",
            result_uuid="result",
        )

        with (
            patch.object(
                client,
                "_connect_candidate",
                side_effect=BleakGATTProtocolError(0xF2),
            ),
            patch.object(client, "_unpair_candidate", new=AsyncMock()) as unpair,
            self.assertRaises(BleakGATTProtocolError),
        ):
            await client._open_candidate(
                device,
                40,
            )

        unpair.assert_not_awaited()

    async def test_connect_error_never_clears_bond_before_qr_verification(self) -> None:
        device = SimpleNamespace(address="40:01:02:0A:C4:A6")
        client = ReverseGattPairingClient(
            service_uuid="service",
            session_uuid="session",
            claim_uuid="claim",
            result_uuid="result",
        )

        with (
            patch.object(
                client,
                "_connect_candidate",
                side_effect=BleakError("Could not pair with device: FAILED"),
            ),
            patch.object(client, "_unpair_candidate", new=AsyncMock()) as unpair,
            self.assertRaises(BleakError),
        ):
            await client._open_candidate(
                device,
                40,
            )

        unpair.assert_not_awaited()

    async def test_does_not_unpair_for_unrelated_gatt_protocol_error(self) -> None:
        device = SimpleNamespace(address="40:01:02:0A:C4:A6")
        client = ReverseGattPairingClient(
            service_uuid="service",
            session_uuid="session",
            claim_uuid="claim",
            result_uuid="result",
        )

        with (
            patch.object(
                client,
                "_connect_candidate",
                side_effect=BleakGATTProtocolError(0x05),
            ),
            patch.object(client, "_unpair_candidate", new=AsyncMock()) as unpair,
            self.assertRaises(BleakGATTProtocolError),
        ):
            await client._open_candidate(
                device,
                40,
            )

        unpair.assert_not_awaited()

    async def test_resets_only_a_qr_verified_stale_bond(self) -> None:
        link = PairingLink(
            session_id="abcdefghijklmnopQRSTUVWX",
            observer_id="dell_cucina",
            expires_at=int(time.time()) + 180,
            secret=bytes(range(32)),
        )
        session = {
            "v": link.version,
            "sid": link.session_id,
            "oid": link.observer_id,
            "exp": link.expires_at,
        }
        claim = {**session, "proof": claim_proof(link)}
        device = SimpleNamespace(address="40:01:02:0A:C4:A6", name="Presence Pair")
        bleak_client = Mock()
        bleak_client.disconnect = AsyncMock()
        bleak_client.pair = AsyncMock(
            side_effect=BleakError("Could not pair with device: FAILED")
        )
        bleak_client.unpair = AsyncMock()
        bleak_client.is_connected = True
        bleak_client.read_gatt_char = AsyncMock(
            side_effect=[json.dumps(session).encode(), json.dumps(claim).encode()]
        )
        client = ReverseGattPairingClient(
            service_uuid="service",
            session_uuid="session",
            claim_uuid="claim",
            result_uuid="result",
        )

        with (
            patch.object(client, "_open_candidate", return_value=bleak_client),
            self.assertRaises(BondResetRequiredError),
        ):
            await client._pair_candidate(
                device,
                link,
                40,
                allow_bond_reset=True,
            )

        bleak_client.unpair.assert_awaited_once()

    async def test_missing_gatt_service_does_not_clear_unverified_bond(self) -> None:
        link = PairingLink(
            session_id="abcdefghijklmnopQRSTUVWX",
            observer_id="dell_cucina",
            expires_at=int(time.time()) + 180,
            secret=bytes(range(32)),
        )
        device = SimpleNamespace(address="51:DE:37:A6:76:32", name="Presence Pair")
        bleak_client = Mock()
        bleak_client.is_connected = True

        async def disconnect() -> None:
            bleak_client.is_connected = False

        bleak_client.disconnect = AsyncMock(side_effect=disconnect)
        bleak_client.services = []
        bleak_client.read_gatt_char = AsyncMock(
            side_effect=BleakCharacteristicNotFoundError("session")
        )
        client = ReverseGattPairingClient(
            service_uuid="service",
            session_uuid="session",
            claim_uuid="claim",
            result_uuid="result",
        )

        with (
            patch.object(client, "_open_candidate", return_value=bleak_client),
            patch.object(client, "_unpair_candidate", new=AsyncMock()) as unpair,
            self.assertRaises(BleakCharacteristicNotFoundError),
        ):
            await client._pair_candidate(
                device,
                link,
                40,
                allow_bond_reset=True,
            )

        bleak_client.disconnect.assert_awaited_once()
        unpair.assert_not_awaited()

    async def test_missing_presence_service_does_not_clear_unverified_bond(
        self,
    ) -> None:
        link = PairingLink(
            session_id="abcdefghijklmnopQRSTUVWX",
            observer_id="dell_cucina",
            expires_at=int(time.time()) + 180,
            secret=bytes(range(32)),
        )
        device = SimpleNamespace(address="51:DE:37:A6:76:32", name="Presence Pair")
        unrelated_characteristic = SimpleNamespace(uuid="battery")
        unrelated_service = SimpleNamespace(
            uuid="battery-service",
            characteristics=[unrelated_characteristic],
        )
        bleak_client = Mock(
            is_connected=True,
            services=[unrelated_service],
            read_gatt_char=AsyncMock(
                side_effect=BleakCharacteristicNotFoundError("session")
            ),
            disconnect=AsyncMock(),
        )
        client = ReverseGattPairingClient(
            service_uuid="service",
            session_uuid="session",
            claim_uuid="claim",
            result_uuid="result",
        )

        with (
            patch.object(client, "_open_candidate", return_value=bleak_client),
            patch.object(client, "_unpair_candidate", new=AsyncMock()) as unpair,
            self.assertRaises(BleakCharacteristicNotFoundError),
        ):
            await client._pair_candidate(
                device,
                link,
                40,
                allow_bond_reset=True,
            )

        unpair.assert_not_awaited()

    async def test_bond_reset_budget_is_scoped_to_private_address(self) -> None:
        link = PairingLink(
            session_id="abcdefghijklmnopQRSTUVWX",
            observer_id="dell_cucina",
            expires_at=int(time.time()) + 180,
            secret=bytes(range(32)),
        )
        stale_device = SimpleNamespace(
            address="40:01:02:0A:C4:A6",
            name="Presence Pair",
        )
        current_device = SimpleNamespace(
            address="51:DE:37:A6:76:32",
            name="Presence Pair",
        )
        result = ReverseGattResult(
            address=current_device.address,
            name=current_device.name,
        )
        client = ReverseGattPairingClient(
            service_uuid="service",
            session_uuid="session",
            claim_uuid="claim",
            result_uuid="result",
        )

        with (
            patch.object(
                BleakScanner,
                "find_device_by_filter",
                new=AsyncMock(side_effect=[stale_device, current_device]),
            ),
            patch.object(
                client,
                "_pair_candidate",
                new=AsyncMock(
                    side_effect=[
                        BondResetRequiredError("retry", "iphone_bond_reset"),
                        result,
                    ]
                ),
            ) as pair_candidate,
            patch("reverse_gatt_client.asyncio.sleep", new=AsyncMock()),
        ):
            actual = await client.async_pair(link, 30)

        self.assertEqual(actual, result)
        self.assertTrue(pair_candidate.await_args_list[0].kwargs["allow_bond_reset"])
        self.assertTrue(pair_candidate.await_args_list[1].kwargs["allow_bond_reset"])
        self.assertIn("attempt_expires_at", client.lease_payload)

    async def test_same_private_address_resets_at_most_once(self) -> None:
        link = PairingLink(
            session_id="abcdefghijklmnopQRSTUVWX",
            observer_id="dell_cucina",
            expires_at=int(time.time()) + 180,
            secret=bytes(range(32)),
        )
        device = SimpleNamespace(
            address="40:01:02:0A:C4:A6",
            name="Presence Pair",
        )
        result = ReverseGattResult(address=device.address, name=device.name)
        client = ReverseGattPairingClient(
            service_uuid="service",
            session_uuid="session",
            claim_uuid="claim",
            result_uuid="result",
        )
        reset_permissions: list[bool] = []

        async def pair_candidate(
            *_args: object,
            **kwargs: object,
        ) -> ReverseGattResult:
            reset_permissions.append(bool(kwargs["allow_bond_reset"]))
            if len(reset_permissions) == 1:
                raise BondResetRequiredError("retry", "iphone_bond_reset")
            return result

        with (
            patch.object(
                BleakScanner,
                "find_device_by_filter",
                new=AsyncMock(side_effect=[device, device]),
            ),
            patch.object(client, "_pair_candidate", side_effect=pair_candidate),
            patch("reverse_gatt_client.asyncio.sleep", new=AsyncMock()),
        ):
            actual = await client.async_pair(link, 30)

        self.assertEqual(actual, result)
        self.assertEqual(reset_permissions, [True, False])

    def test_completion_lease_is_separate_from_qr_handoff(self) -> None:
        client = ReverseGattPairingClient(
            service_uuid="service",
            session_uuid="session",
            claim_uuid="claim",
            result_uuid="result",
        )
        with (
            patch("reverse_gatt_client.time.monotonic", return_value=100.0),
            patch("reverse_gatt_client.time.time", return_value=1_000.0),
        ):
            client.start_handoff_lease()
            client._start_completion_lease()

        self.assertEqual(client.lease_payload["attempt_expires_at"], 2_800)
        self.assertEqual(client.lease_payload["completion_expires_at"], 2_800)


if __name__ == "__main__":
    unittest.main()
