"""Native reconnect ownership must be released before Bleak drops its handles."""

import asyncio
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from reverse_gatt_client import ReverseGattPairingClient


class ConnectionCleanupTest(unittest.IsolatedAsyncioTestCase):
    async def run_case(self, cancelled):
        session = SimpleNamespace(maintain_connection=True)
        backend = SimpleNamespace(_session=session, _retry_on_services_changed=False)
        started = asyncio.Event()
        state_at_native_close = []

        async def connect():
            started.set()
            try:
                await asyncio.Event().wait()
            finally:
                state_at_native_close.append(session.maintain_connection)
                backend._session = None

        transport = SimpleNamespace(
            _backend=backend, connect=connect, disconnect=AsyncMock()
        )
        client = ReverseGattPairingClient(
            service_uuid="service",
            session_uuid="session",
            claim_uuid="claim",
            result_uuid="result",
        )
        with (
            patch("reverse_gatt_client.sys.platform", "win32"),
            patch("reverse_gatt_client.BleakClient", return_value=transport),
        ):
            task = asyncio.create_task(
                client._connect_candidate(
                    SimpleNamespace(address="phone"),
                    connection_timeout=0.03,
                    address_type="random",
                    filter_services=True,
                    use_cached_services=False,
                    pair_before_discovery=False,
                )
            )
            if cancelled:
                await started.wait()
                task.cancel()
            with self.assertRaises(
                asyncio.CancelledError if cancelled else TimeoutError
            ):
                await task
        self.assertEqual(state_at_native_close, [False])
        transport.disconnect.assert_awaited_once()

    async def test_timeout_releases_maintain_connection_before_native_close(self):
        await self.run_case(False)

    async def test_cancellation_releases_maintain_connection_before_native_close(self):
        await self.run_case(True)

    async def test_authenticated_route_enables_winrt_service_change_retry(self):
        backend = SimpleNamespace(
            _session=SimpleNamespace(), _retry_on_services_changed=False
        )
        transport = SimpleNamespace(
            _backend=backend,
            connect=AsyncMock(),
            disconnect=AsyncMock(),
        )
        client = ReverseGattPairingClient(
            service_uuid="service",
            session_uuid="session",
            claim_uuid="claim",
            result_uuid="result",
        )
        with (
            patch("reverse_gatt_client.sys.platform", "win32"),
            patch("reverse_gatt_client.BleakClient", return_value=transport),
        ):
            await client._connect_candidate(
                SimpleNamespace(address="phone"),
                connection_timeout=1,
                address_type="random",
                filter_services=False,
                use_cached_services=False,
                pair_before_discovery=True,
            )
        self.assertTrue(backend._retry_on_services_changed)
