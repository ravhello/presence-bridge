from __future__ import annotations

import json
import time
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

from protocol import PairingLink, acceptance_proof, claim_proof
from reverse_gatt_client import ReverseGattPairingClient


class ReverseGattPairingClientTest(unittest.IsolatedAsyncioTestCase):
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
        bleak_client = Mock()
        bleak_client.connect = AsyncMock()
        bleak_client.disconnect = AsyncMock()
        bleak_client.pair = AsyncMock()
        bleak_client.is_connected = True
        bleak_client.read_gatt_char = AsyncMock(
            side_effect=[json.dumps(session).encode(), json.dumps(claim).encode()]
        )
        bleak_client.write_gatt_char = AsyncMock()
        progress: list[str] = []
        client = ReverseGattPairingClient(
            service_uuid="service",
            session_uuid="session",
            claim_uuid="claim",
            result_uuid="result",
            progress_callback=lambda code, _message: progress.append(code),
        )

        with patch("reverse_gatt_client.BleakClient", return_value=bleak_client):
            result = await client._pair_candidate(device, link, 40)

        self.assertEqual(result.address, device.address)
        bleak_client.pair.assert_awaited_once()
        acknowledgement = json.loads(
            bleak_client.write_gatt_char.await_args.args[1].decode()
        )
        self.assertEqual(acknowledgement["status"], "accepted")
        self.assertEqual(acknowledgement["proof"], acceptance_proof(link))
        self.assertEqual(progress[-1], "iphone_claim_accepted")


if __name__ == "__main__":
    unittest.main()
