from __future__ import annotations

import asyncio
import json
import logging
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
    EncryptedAcknowledgementRejectedError,
    GattMetadataLogFilter,
    PairingDiagnosticError,
    ReverseGattError,
    ReverseGattPairingClient,
    ReverseGattResult,
    ReverseGattTimeoutError,
)


class ReverseGattPairingClientTest(unittest.IsolatedAsyncioTestCase):
    async def test_only_a_reused_bond_with_explicit_encryption_rejection_is_repaired(
        self,
    ):
        for reused, allow_reset, failure in (
            (True, True, BleakGATTProtocolError(0x05)),
            (True, False, BleakGATTProtocolError(0x05)),
            (False, True, BleakGATTProtocolError(0x05)),
            (True, True, TimeoutError()),
        ):
            link = PairingLink(
                session_id="abcdefghijklmnopQRSTUVWX",
                observer_id="receiver",
                expires_at=int(time.time()) + 180,
                secret=bytes(range(32)),
            )
            session = {
                "v": link.version,
                "sid": link.session_id,
                "oid": link.observer_id,
                "exp": link.expires_at,
            }
            policy = AsyncMock(return_value=True)
            policy.reused_bond = reused
            native = SimpleNamespace(
                write_gatt_char=AsyncMock(side_effect=failure), unpair=AsyncMock()
            )
            client = ReverseGattPairingClient(
                service_uuid="service",
                session_uuid="session",
                claim_uuid="claim",
                result_uuid="result",
                pairing_probe=policy,
            )
            with (
                patch.object(
                    client, "_open_candidate", new=AsyncMock(return_value=native)
                ),
                patch.object(
                    client,
                    "_read_phone_payload",
                    new=AsyncMock(
                        side_effect=[
                            json.dumps(session).encode(),
                            json.dumps(
                                {**session, "proof": claim_proof(link)}
                            ).encode(),
                        ]
                    ),
                ),
                patch.object(client, "_require_ack_encryption"),
                patch.object(client, "_release_client", new=AsyncMock()),
                patch("reverse_gatt_client.asyncio.sleep", new=AsyncMock()),
            ):
                with self.assertRaises(ReverseGattError) as raised:
                    await client._pair_candidate(
                        SimpleNamespace(address="AA:BB:CC:DD:EE:FF"),
                        link,
                        30,
                        allow_bond_reset=allow_reset,
                    )
                if (
                    reused
                    and allow_reset
                    and isinstance(failure, BleakGATTProtocolError)
                ):
                    self.assertIsInstance(raised.exception, BondResetRequiredError)
                    native.unpair.assert_awaited_once()
                    self.assertFalse(client._secure_bond_confirmed)
                else:
                    native.unpair.assert_not_awaited()

    async def test_numeric_probe_preserves_claim_and_acknowledgement_gates(self):
        for scenario in ("success", "invalid_claim", "pair_failed", "ack_failed"):
            with self.subTest(scenario=scenario):
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
                events = []

                async def pair(*_args, events=events, scenario=scenario):
                    events.append("pair")
                    if scenario == "pair_failed":
                        raise PairingDiagnosticError(
                            "rejected", "numeric_pairing_not_authenticated"
                        )
                    return True

                async def write(*_args, events=events, scenario=scenario, **_kwargs):
                    events.append("ack")
                    if scenario == "ack_failed":
                        raise BleakError("Unreachable")

                native = SimpleNamespace(
                    write_gatt_char=AsyncMock(side_effect=write),
                    pair=AsyncMock(),
                    unpair=AsyncMock(),
                )
                pairing_probe = AsyncMock(side_effect=pair)
                client = ReverseGattPairingClient(
                    service_uuid="service",
                    session_uuid="session",
                    claim_uuid="claim",
                    result_uuid="result",
                    pairing_probe=pairing_probe,
                )
                proof = "invalid" if scenario == "invalid_claim" else claim_proof(link)
                with (
                    patch.object(
                        client, "_open_candidate", new=AsyncMock(return_value=native)
                    ),
                    patch.object(
                        client,
                        "_read_phone_payload",
                        new=AsyncMock(
                            side_effect=[
                                json.dumps(session).encode(),
                                json.dumps({**session, "proof": proof}).encode(),
                            ]
                        ),
                    ),
                    patch.object(client, "_require_ack_encryption") as protection,
                    patch.object(client, "_release_client", new=AsyncMock()) as release,
                    patch("reverse_gatt_client.asyncio.sleep", new=AsyncMock()),
                ):
                    if scenario == "success":
                        result = await client._pair_candidate(
                            SimpleNamespace(address="AA:BB:CC:DD:EE:FF", name="iPhone"),
                            link,
                            30,
                            allow_bond_reset=False,
                        )
                        self.assertTrue(result.secure_exchange_complete)
                        self.assertEqual(events, ["pair", "ack"])
                        self.assertTrue(
                            protection.call_args.kwargs["require_authentication"]
                        )
                    else:
                        with self.assertRaises(ReverseGattError) as raised:
                            await client._pair_candidate(
                                SimpleNamespace(
                                    address="AA:BB:CC:DD:EE:FF", name="iPhone"
                                ),
                                link,
                                30,
                                allow_bond_reset=False,
                            )
                        if scenario == "invalid_claim":
                            pairing_probe.assert_not_awaited()
                            native.write_gatt_char.assert_not_awaited()
                        elif scenario == "pair_failed":
                            native.write_gatt_char.assert_not_awaited()
                        else:
                            self.assertEqual(
                                raised.exception.detail_code,
                                "numeric_comparison_ack_failed",
                            )
                    release.assert_awaited_once_with(native)
                    native.pair.assert_not_awaited()
                    native.unpair.assert_not_awaited()

    async def test_cached_read_failure_after_bond_forces_fresh_verified_exchange(self):
        for failed_payload in ("session", "claim"):
            with self.subTest(failed_payload=failed_payload):
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
                values = [
                    json.dumps(session).encode(),
                    json.dumps({**session, "proof": claim_proof(link)}).encode(),
                ]
                service = SimpleNamespace(
                    uuid=pairing_service_uuid(link),
                    characteristics=[SimpleNamespace(uuid="session")],
                )

                def transport(reads, service=service):
                    return SimpleNamespace(
                        services=[service],
                        is_connected=True,
                        read_gatt_char=AsyncMock(side_effect=reads),
                        write_gatt_char=AsyncMock(),
                        pair=AsyncMock(),
                        unpair=AsyncMock(),
                        disconnect=AsyncMock(),
                    )

                initial = transport(values)
                initial.write_gatt_char.side_effect = BleakError("Unreachable")

                async def accept_bond(initial=initial):
                    initial.is_connected = False

                initial.pair.side_effect = accept_bond
                stale_values = (
                    [OSError("Windows cancelled the request")]
                    if failed_payload == "session"
                    else [values[0], OSError("Windows cancelled the request")]
                )
                cached = transport(stale_values)
                fresh = transport(values)
                device = SimpleNamespace(address="AA:BB:CC:DD:EE:FF", name="iPhone")
                client = ReverseGattPairingClient(
                    service_uuid="service",
                    session_uuid="session",
                    claim_uuid="claim",
                    result_uuid="result",
                )

                async def find_device(filter_func, device=device, link=link, **_kwargs):
                    self.assertTrue(
                        filter_func(
                            device,
                            SimpleNamespace(
                                service_uuids=[pairing_service_uuid(link)],
                                rssi=-60,
                            ),
                        )
                    )
                    return device

                with (
                    patch.object(
                        BleakScanner,
                        "find_device_by_filter",
                        new=AsyncMock(side_effect=find_device),
                    ),
                    patch.object(
                        client,
                        "_connect_candidate",
                        new=AsyncMock(side_effect=[initial, cached, fresh]),
                    ) as connect,
                    patch.object(client, "_require_ack_encryption"),
                    patch.object(client, "_windows_reports_paired", return_value=False),
                    patch("reverse_gatt_client.asyncio.sleep", new=AsyncMock()),
                ):
                    result = await client.async_pair(link, 180)

                self.assertTrue(result.secure_exchange_complete)
                self.assertEqual(
                    [
                        call.kwargs["use_cached_services"]
                        for call in connect.await_args_list
                    ],
                    [False, True, False],
                )
                self.assertTrue(
                    all(
                        call.kwargs["filter_services"]
                        for call in connect.await_args_list
                    )
                )
                self.assertTrue(
                    all(
                        not call.kwargs["pair_before_discovery"]
                        for call in connect.await_args_list
                    )
                )
                initial.pair.assert_awaited_once()
                cached.write_gatt_char.assert_not_awaited()
                fresh.write_gatt_char.assert_awaited_once()
                fresh.pair.assert_not_awaited()
                for peer in (initial, cached, fresh):
                    peer.unpair.assert_not_awaited()
                    peer.disconnect.assert_awaited_once()

    async def test_payload_read_timeout_cancels_operation_and_scopes_fresh_discovery(
        self,
    ):
        cancelled = asyncio.Event()

        async def read(_uuid):
            try:
                await asyncio.Event().wait()
            finally:
                cancelled.set()

        client = ReverseGattPairingClient(
            service_uuid="service",
            session_uuid="session",
            claim_uuid="claim",
            result_uuid="result",
        )
        device = SimpleNamespace(address="AA:BB:CC:DD:EE:FF")
        with (
            patch("reverse_gatt_client.GATT_PAYLOAD_READ_TIMEOUT_SECONDS", 0.02),
            self.assertRaises(TimeoutError),
        ):
            await client._read_phone_payload(
                SimpleNamespace(read_gatt_char=read), device, "session"
            )
        self.assertTrue(cancelled.is_set())
        self.assertEqual(client._fresh_discovery_addresses, {device.address.casefold()})

    async def test_cancelled_read_does_not_schedule_recovery(self):
        client = ReverseGattPairingClient(
            service_uuid="service",
            session_uuid="session",
            claim_uuid="claim",
            result_uuid="result",
        )
        with self.assertRaises(asyncio.CancelledError):
            await client._read_phone_payload(
                SimpleNamespace(
                    read_gatt_char=AsyncMock(side_effect=asyncio.CancelledError)
                ),
                SimpleNamespace(address="phone"),
                "session",
            )
        self.assertEqual(client._fresh_discovery_addresses, set())

    def test_terminal_link_failure_is_not_reported_as_a_timeout(self):
        self.assertEqual(
            ReverseGattError("link failed", "iphone_bond_link_failed").terminal_state,
            "error",
        )
        self.assertEqual(
            ReverseGattTimeoutError(
                "deadline", "interactive_receiver_timeout"
            ).terminal_state,
            "timeout",
        )

    def test_gatt_diagnostics_omit_payload_but_preserve_error_status(self):
        error = BleakError(
            "Could not write value b'private-proof' to characteristic 003E: AccessDenied"
        )
        summary = ReverseGattPairingClient._error_summary(error)
        self.assertNotIn("private-proof", summary)
        self.assertIn("AccessDenied", summary)
        guard = GattMetadataLogFilter()
        self.assertFalse(
            guard.filter(
                logging.LogRecord(
                    "bleak",
                    10,
                    "",
                    1,
                    "Read Characteristic %04X : %s",
                    (62, b"private-proof"),
                    None,
                )
            )
        )
        self.assertTrue(
            guard.filter(
                logging.LogRecord("bleak", 10, "", 1, "services changed", (), None)
            )
        )

    async def test_connect_timeout_cancels_discovery_before_next_route(self):
        backend = SimpleNamespace(_retry_on_services_changed=True)
        discovery_cancelled = asyncio.Event()
        discovery_started = asyncio.Event()
        children = []

        async def discover():
            discovery_started.set()
            try:
                await asyncio.Event().wait()
            finally:
                discovery_cancelled.set()

        async def connect():
            if backend._retry_on_services_changed:
                children.append(asyncio.create_task(discover()))
                await asyncio.wait(children)
            else:
                await discover()

        transport = SimpleNamespace(
            _backend=backend, connect=connect, disconnect=AsyncMock()
        )
        client = ReverseGattPairingClient(
            service_uuid="service",
            session_uuid="session",
            claim_uuid="claim",
            result_uuid="result",
        )
        try:
            with (
                patch("reverse_gatt_client.sys.platform", "win32"),
                patch("reverse_gatt_client.BleakClient", return_value=transport),
                self.assertRaises(TimeoutError),
            ):
                await client._connect_candidate(
                    SimpleNamespace(address="phone"),
                    connection_timeout=0.03,
                    address_type="random",
                    filter_services=True,
                    use_cached_services=False,
                    pair_before_discovery=False,
                )
            self.assertTrue(discovery_started.is_set())
            self.assertTrue(discovery_cancelled.is_set())
            self.assertEqual(children, [])
            transport.disconnect.assert_awaited_once()
        finally:
            for child in children:
                child.cancel()
            if children:
                await asyncio.gather(*children, return_exceptions=True)

    async def test_post_bond_discovery_failure_is_bounded_without_unpair(self):
        client = ReverseGattPairingClient(
            service_uuid="service",
            session_uuid="session",
            claim_uuid="claim",
            result_uuid="result",
        )
        link = PairingLink(
            session_id="abcdefghijklmnopQRSTUVWX",
            observer_id="dell_cucina",
            expires_at=int(time.time()) + 180,
            secret=bytes(range(32)),
        )

        async def fail(*_args, **_kwargs):
            client._secure_bond_confirmed = True
            raise TimeoutError("discovery unavailable")

        with (
            patch.object(
                BleakScanner,
                "find_device_by_filter",
                new=AsyncMock(return_value=SimpleNamespace(address="phone")),
            ),
            patch.object(
                client, "_pair_candidate", new=AsyncMock(side_effect=fail)
            ) as pair,
            patch.object(client, "_unpair_candidate", new=AsyncMock()) as unpair,
            patch("reverse_gatt_client.asyncio.sleep", new=AsyncMock()),
            self.assertRaises(ReverseGattError) as failure,
        ):
            await client.async_pair(link, 180)
        self.assertEqual(failure.exception.detail_code, "iphone_bond_link_failed")
        self.assertEqual(pair.await_count, 2)
        unpair.assert_not_awaited()

    def test_ack_requires_link_encryption_without_downgrading_authentication(self):
        client = ReverseGattPairingClient(
            service_uuid="service",
            session_uuid="session",
            claim_uuid="claim",
            result_uuid="result",
        )
        levels = SimpleNamespace(
            PLAIN=0,
            AUTHENTICATION_REQUIRED=1,
            ENCRYPTION_REQUIRED=2,
            ENCRYPTION_AND_AUTHENTICATION_REQUIRED=3,
        )
        for initial, expected in ((0, 2), (1, 3), (2, 2), (3, 3)):
            native = SimpleNamespace(protection_level=initial)
            transport = Mock()
            transport.services.get_characteristic.return_value = SimpleNamespace(
                obj=native
            )
            with patch("reverse_gatt_client.WinRTGattProtectionLevel", levels):
                client._require_ack_encryption(transport)
            transport.services.get_characteristic.assert_called_once_with("result")
            self.assertEqual(native.protection_level, expected)

    async def test_authentication_upgrade_requires_a_successful_protected_write(self):
        client = ReverseGattPairingClient(
            service_uuid="service",
            session_uuid="session",
            claim_uuid="claim",
            result_uuid="result",
        )
        phone = SimpleNamespace(address="AA:BB:CC:DD:EE:FF")
        for outcome in (
            None,
            BleakGATTProtocolError(0x05),
            BleakError("Unreachable"),
            TimeoutError(),
            asyncio.CancelledError(),
        ):
            with self.subTest(outcome=type(outcome).__name__):
                client._authenticated_ack_addresses.clear()
                transport = SimpleNamespace(
                    write_gatt_char=AsyncMock(side_effect=outcome)
                )
                with (
                    patch("reverse_gatt_client.WinRTGattProtectionLevel", object()),
                    patch.object(client, "_require_ack_encryption") as protection,
                ):
                    if isinstance(outcome, asyncio.CancelledError):
                        with self.assertRaises(asyncio.CancelledError):
                            await client._retry_ack_with_authentication(
                                transport,
                                phone,
                                b"test",
                                BleakGATTProtocolError(0x05),
                                time.monotonic() + 20,
                            )
                    elif outcome is not None:
                        with self.assertRaises(
                            EncryptedAcknowledgementRejectedError
                        ) as failure:
                            await client._retry_ack_with_authentication(
                                transport,
                                phone,
                                b"test",
                                BleakGATTProtocolError(0x05),
                                time.monotonic() + 20,
                            )
                        self.assertIs(failure.exception.__cause__, outcome)
                    else:
                        result = await client._retry_ack_with_authentication(
                            transport,
                            phone,
                            b"test",
                            BleakGATTProtocolError(0x05),
                            time.monotonic() + 20,
                        )
                        self.assertIs(result, outcome)
                protection.assert_called_once_with(
                    transport, require_authentication=True
                )
                transport.write_gatt_char.assert_awaited_once_with(
                    "result", b"test", response=True
                )

    async def test_authentication_upgrade_is_bounded_and_not_repeated(self):
        client = ReverseGattPairingClient(
            service_uuid="service",
            session_uuid="session",
            claim_uuid="claim",
            result_uuid="result",
        )
        phone = SimpleNamespace(address="AA:BB:CC:DD:EE:FF")
        transport = SimpleNamespace(write_gatt_char=AsyncMock())
        rejected = BleakGATTProtocolError(0x05)
        with (
            patch("reverse_gatt_client.WinRTGattProtectionLevel", object()),
            patch.object(client, "_require_ack_encryption"),
        ):
            self.assertIsNone(
                await client._retry_ack_with_authentication(
                    transport,
                    phone,
                    b"test",
                    rejected,
                    time.monotonic() + 20,
                )
            )
            self.assertIs(
                await client._retry_ack_with_authentication(
                    transport,
                    phone,
                    b"test",
                    rejected,
                    time.monotonic() + 20,
                ),
                rejected,
            )
            for error, deadline in (
                (rejected, time.monotonic() - 1),
                (BleakGATTProtocolError(0x0F), time.monotonic() + 20),
                (TimeoutError(), time.monotonic() + 20),
            ):
                self.assertIs(
                    await client._retry_ack_with_authentication(
                        transport,
                        SimpleNamespace(address="other"),
                        b"test",
                        error,
                        deadline,
                    ),
                    error,
                )
        transport.write_gatt_char.assert_awaited_once()

    def test_repeated_encrypted_ack_rejection_is_terminal_and_scoped_to_phone(self):
        client = ReverseGattPairingClient(
            service_uuid="service",
            session_uuid="session",
            claim_uuid="claim",
            result_uuid="result",
        )
        phone = SimpleNamespace(address="AA:BB:CC:DD:EE:FF")
        other = SimpleNamespace(address="11:22:33:44:55:66")
        client._record_ack_rejection(phone, TimeoutError())
        self.assertEqual(client._ack_auth_failures, {})
        client._record_ack_rejection(phone, BleakGATTProtocolError(0x05))
        client._record_ack_rejection(phone, BleakGATTProtocolError(0x0F))
        client._record_ack_rejection(other, BleakGATTProtocolError(0x05))
        with self.assertRaises(EncryptedAcknowledgementRejectedError) as failure:
            client._record_ack_rejection(phone, BleakGATTProtocolError(0x05))
        self.assertEqual(failure.exception.detail_code, "iphone_encryption_failed")

    async def test_non_att_post_bond_write_failures_stop_after_three_attempts(self):
        client = ReverseGattPairingClient(
            service_uuid="service",
            session_uuid="session",
            claim_uuid="claim",
            result_uuid="result",
        )
        link = PairingLink(
            session_id="abcdefghijklmnopQRSTUVWX",
            observer_id="dell_cucina",
            expires_at=int(time.time()) + 180,
            secret=bytes(range(32)),
        )

        async def fail(*_args, **_kwargs):
            client._secure_bond_confirmed = True
            raise ReverseGattError(
                "Protected write unreachable", "iphone_bond_reconnecting"
            )

        with (
            patch.object(
                BleakScanner,
                "find_device_by_filter",
                new=AsyncMock(
                    return_value=SimpleNamespace(address="AA:BB:CC:DD:EE:FF"),
                ),
            ),
            patch.object(
                client, "_pair_candidate", new=AsyncMock(side_effect=fail)
            ) as pair,
            patch.object(client, "_unpair_candidate", new=AsyncMock()) as unpair,
            patch("reverse_gatt_client.asyncio.sleep", new=AsyncMock()),
            self.assertRaises(ReverseGattError) as failure,
        ):
            await client.async_pair(link, 60)
        self.assertEqual(failure.exception.detail_code, "iphone_bond_link_failed")
        self.assertEqual(pair.await_count, 3)
        unpair.assert_not_awaited()

    async def test_terminal_encryption_error_does_not_retry_or_reset_bond(self):
        client = ReverseGattPairingClient(
            service_uuid="service",
            session_uuid="session",
            claim_uuid="claim",
            result_uuid="result",
        )
        link = PairingLink(
            session_id="abcdefghijklmnopQRSTUVWX",
            observer_id="dell_cucina",
            expires_at=int(time.time()) + 180,
            secret=bytes(range(32)),
        )
        device = SimpleNamespace(address="AA:BB:CC:DD:EE:FF")
        failure = EncryptedAcknowledgementRejectedError(
            "Rejected", "iphone_encryption_failed"
        )
        with (
            patch.object(
                BleakScanner,
                "find_device_by_filter",
                new=AsyncMock(return_value=device),
            ),
            patch.object(
                client, "_pair_candidate", new=AsyncMock(side_effect=failure)
            ) as pair,
            patch.object(client, "_unpair_candidate", new=AsyncMock()) as unpair,
            self.assertRaises(EncryptedAcknowledgementRejectedError),
        ):
            await client.async_pair(link, 60)
        pair.assert_awaited_once()
        unpair.assert_not_awaited()

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
        self.assertTrue(result.secure_exchange_complete)
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

    async def test_closed_ack_channel_after_bond_reconnects_without_unpairing(
        self,
    ) -> None:
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
            patch("reverse_gatt_client.WinRTGattProtectionLevel", object()),
            patch.object(client, "_require_ack_encryption") as protection,
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

        self.assertEqual(raised.exception.detail_code, "iphone_encryption_failed")
        bleak_client.unpair.assert_not_awaited()
        self.assertEqual(bleak_client.write_gatt_char.await_count, 2)
        protection.assert_called_with(bleak_client, require_authentication=True)

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

    async def test_disables_unowned_winrt_service_change_retry_before_connect(
        self,
    ) -> None:
        device = SimpleNamespace(address="40:01:02:0A:C4:A6")
        backend = SimpleNamespace(_retry_on_services_changed=True)
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

        self.assertFalse(backend._retry_on_services_changed)
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
        self.assertFalse(connect.await_args.kwargs["use_cached_services"])
        self.assertTrue(connect.await_args.kwargs["filter_services"])

    async def test_confirmed_bond_reuses_cached_qr_services_without_pairing_again(
        self,
    ) -> None:
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
        self.assertFalse(connect.await_args.kwargs["pair_before_discovery"])
        self.assertTrue(connect.await_args.kwargs["use_cached_services"])
        self.assertTrue(connect.await_args.kwargs["filter_services"])

    async def test_qr_discovery_has_one_bounded_full_lookup_fallback(
        self,
    ) -> None:
        client = ReverseGattPairingClient(
            service_uuid="legacy",
            session_uuid="session",
            claim_uuid="claim",
            result_uuid="result",
        )
        client._session_service_uuid = "active-qr"
        client._matched_service_uuid = "active-qr"
        with (
            patch.object(
                client, "_connect_candidate", new=AsyncMock(side_effect=TimeoutError())
            ) as connect,
            patch("reverse_gatt_client.asyncio.sleep", new=AsyncMock()),
        ):
            with self.assertRaises(TimeoutError):
                await client._open_candidate(SimpleNamespace(address="test"), 90)
            self.assertEqual(connect.await_count, 2)
            self.assertTrue(
                all(
                    call.kwargs["connection_timeout"] <= 10
                    for call in connect.await_args_list
                )
            )
            connect.reset_mock()
            with self.assertRaises(TimeoutError):
                await client._open_candidate(SimpleNamespace(address="test"), 90)
            self.assertEqual(connect.await_count, 3)
            self.assertEqual(
                [c.kwargs["filter_services"] for c in connect.await_args_list],
                [True, True, False],
            )
            self.assertLessEqual(
                connect.await_args_list[-1].kwargs["connection_timeout"], 12
            )
            self.assertTrue(
                all(
                    not call.kwargs["pair_before_discovery"]
                    for call in connect.await_args_list
                )
            )

    async def test_release_stops_reconnects_even_when_link_is_not_connected(
        self,
    ) -> None:
        session = SimpleNamespace(maintain_connection=True)
        client = SimpleNamespace(
            is_connected=False,
            _backend=SimpleNamespace(_session=session),
            disconnect=AsyncMock(),
        )
        with patch("reverse_gatt_client.sys.platform", "win32"):
            await ReverseGattPairingClient._release_client(client)
        self.assertFalse(session.maintain_connection)
        client.disconnect.assert_awaited_once()

    async def test_missing_qr_service_does_not_erase_saved_bond(self) -> None:
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
        unpair.assert_not_awaited()
        self.assertEqual(pair_candidate.await_count, 2)
        self.assertTrue(pair_candidate.await_args_list[0].kwargs["allow_bond_reset"])
        self.assertTrue(pair_candidate.await_args_list[1].kwargs["allow_bond_reset"])

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

    def test_completion_lease_never_extends_the_five_minute_attempt(self) -> None:
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
        with (
            patch("reverse_gatt_client.time.monotonic", return_value=390.0),
            patch("reverse_gatt_client.time.time", return_value=1_290.0),
        ):
            client._start_completion_lease()

        self.assertEqual(client.lease_payload["attempt_expires_at"], 1_300)
        self.assertEqual(client.lease_payload["completion_expires_at"], 1_300)
        self.assertEqual(client._completion_deadline, 400.0)


if __name__ == "__main__":
    unittest.main()
