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
from protocol import PairingLink, acceptance_proof, claim_proof
from reverse_gatt_client import (
    BondResetRequiredError,
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

    def test_matches_presence_pair_name_when_ios_omits_uuid(self) -> None:
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

        self.assertTrue(
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

    def test_matches_truncated_ios_scan_response_name(self) -> None:
        client = ReverseGattPairingClient(
            service_uuid="service",
            session_uuid="session",
            claim_uuid="claim",
            result_uuid="result",
        )

        self.assertTrue(
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
        initial_client.pair = AsyncMock()
        initial_client.unpair = AsyncMock()
        initial_client.is_connected = True
        initial_client.read_gatt_char = AsyncMock(
            side_effect=[json.dumps(session).encode(), json.dumps(claim).encode()]
        )
        initial_client.write_gatt_char = AsyncMock()
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
        initial_client.pair.assert_awaited_once()
        initial_client.disconnect.assert_awaited_once()
        acknowledgement = json.loads(
            initial_client.write_gatt_char.await_args.args[1].decode()
        )
        self.assertEqual(acknowledgement["status"], "accepted")
        self.assertEqual(acknowledgement["proof"], acceptance_proof(link))
        self.assertEqual(progress[-1], "iphone_claim_accepted")

    async def test_pairing_disconnect_reconnects_without_second_prompt(self) -> None:
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
        initial_client = Mock(
            is_connected=True,
            read_gatt_char=AsyncMock(
                side_effect=[
                    json.dumps(session).encode(),
                    BleakGATTProtocolError(0x05),
                    BleakGATTProtocolError(0x05),
                    BleakGATTProtocolError(0x05),
                    BleakGATTProtocolError(0x05),
                ]
            ),
            pair=AsyncMock(),
            unpair=AsyncMock(),
            disconnect=AsyncMock(),
        )
        bonded_client = Mock(
            is_connected=True,
            read_gatt_char=AsyncMock(
                side_effect=[json.dumps(session).encode(), json.dumps(claim).encode()]
            ),
            write_gatt_char=AsyncMock(),
            disconnect=AsyncMock(),
        )
        client = ReverseGattPairingClient(
            service_uuid="service",
            session_uuid="session",
            claim_uuid="claim",
            result_uuid="result",
        )

        with (
            patch.object(
                client,
                "_open_candidate",
                side_effect=[
                    initial_client,
                    OSError("pairing transition disconnected GATT"),
                    bonded_client,
                ],
            ) as open_candidate,
            patch("reverse_gatt_client.asyncio.sleep", new=AsyncMock()),
        ):
            result = await client._pair_candidate(
                device,
                link,
                60,
                allow_bond_reset=True,
            )

        self.assertEqual(result.address, device.address)
        self.assertEqual(open_candidate.await_count, 3)
        initial_client.pair.assert_awaited_once()
        initial_client.unpair.assert_not_awaited()
        bonded_client.write_gatt_char.assert_awaited_once()

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

    async def test_open_candidate_tries_native_then_pre_pair(self) -> None:
        device = SimpleNamespace(address="40:01:02:0A:C4:A6")
        connected = Mock()
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
                allow_bond_reset=False,
            )

        self.assertIs(result, connected)
        self.assertFalse(connect.await_args_list[0].kwargs["pair_before_discovery"])
        self.assertTrue(connect.await_args_list[1].kwargs["pair_before_discovery"])
        self.assertIsNone(connect.await_args_list[0].kwargs["use_cached_services"])

    async def test_timeout_clears_one_matching_bond_before_retry(self) -> None:
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
            self.assertRaises(BondResetRequiredError),
        ):
            await client._open_candidate(
                device,
                82,
                allow_bond_reset=True,
            )

        unpair.assert_awaited_once_with(device, address_type="random")

    async def test_resets_stale_bond_when_winrt_fails_before_service_read(self) -> None:
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
            self.assertRaises(BondResetRequiredError),
        ):
            await client._open_candidate(
                device,
                40,
                allow_bond_reset=True,
            )

        unpair.assert_awaited_once_with(device, address_type="random")

    async def test_resets_partial_bond_when_explicit_pairing_fails(self) -> None:
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
            self.assertRaises(BondResetRequiredError),
        ):
            await client._open_candidate(
                device,
                40,
                allow_bond_reset=True,
            )

        unpair.assert_awaited_once_with(device, address_type="random")

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
                allow_bond_reset=True,
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
        device = SimpleNamespace(address="40:01:02:0A:C4:A6", name="Presence Pair")
        bleak_client = Mock()
        bleak_client.disconnect = AsyncMock()
        bleak_client.pair = AsyncMock(
            side_effect=BleakError("Could not pair with device: FAILED")
        )
        bleak_client.unpair = AsyncMock()
        bleak_client.is_connected = True
        bleak_client.read_gatt_char = AsyncMock(
            return_value=json.dumps(session).encode()
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

    async def test_resets_bond_when_windows_hides_entire_gatt_service(self) -> None:
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
            self.assertRaises(BondResetRequiredError),
        ):
            await client._pair_candidate(
                device,
                link,
                40,
                allow_bond_reset=True,
            )

        bleak_client.disconnect.assert_awaited_once()
        unpair.assert_awaited_once_with(device, address_type="random")

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

    async def test_fresh_bond_is_preserved_on_follow_up_attempt(self) -> None:
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

        async def pair_candidate(*_args: object, **kwargs: object) -> ReverseGattResult:
            reset_permissions.append(bool(kwargs["allow_bond_reset"]))
            if len(reset_permissions) == 1:
                client._newly_bonded_addresses.add(device.address.casefold())
                raise BleakCharacteristicNotFoundError("claim")
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
            client._start_handoff_lease()
            client._start_completion_lease()

        self.assertEqual(client.lease_payload["attempt_expires_at"], 1_090)
        self.assertEqual(client.lease_payload["completion_expires_at"], 1_300)


if __name__ == "__main__":
    unittest.main()
