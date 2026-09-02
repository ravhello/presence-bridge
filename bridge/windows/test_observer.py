from __future__ import annotations

import unittest

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa
from observer import (
    ObserverConfig,
    encrypt_pairing_result,
    normalize_address,
    scan_session_is_stale,
    select_new_irk_records,
)


class BlePresenceObserverTest(unittest.TestCase):
    def test_normalize_address(self) -> None:
        self.assertEqual(
            normalize_address("b0:81:84:ed:b2:56"),
            "B0:81:84:ED:B2:56",
        )
        self.assertEqual(normalize_address("not-an-address"), "")

    def test_scanner_stale_timeout_has_bounded_default(self) -> None:
        self.assertEqual(
            ObserverConfig.__dataclass_fields__["scanner_stale_timeout"].default,
            120.0,
        )
        self.assertFalse(scan_session_is_stale(100.0, 180.0, 250.0, 120.0))
        self.assertTrue(scan_session_is_stale(100.0, 180.0, 300.0, 120.0))

    def test_new_irk_records_are_unique_and_exclude_the_baseline(self) -> None:
        baseline = [{"irk": "00" * 16, "registry_leaf": "AABBCCDDEEFF"}]
        current = [
            *baseline,
            {"irk": "11" * 16, "registry_leaf": "112233445566"},
            {"irk": "11" * 16, "registry_leaf": "665544332211"},
        ]
        selected = select_new_irk_records(baseline, current)
        self.assertEqual(len(selected), 1)
        self.assertEqual(selected[0]["irk"], "11" * 16)

    def test_pairing_result_is_encrypted_for_home_assistant(self) -> None:
        private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        public_der = private_key.public_key().public_bytes(
            serialization.Encoding.DER,
            serialization.PublicFormat.SubjectPublicKeyInfo,
        )
        encoded = encrypt_pairing_result(
            __import__("base64").b64encode(public_der).decode("ascii"),
            {"irk": "AA" * 16},
        )
        plaintext = private_key.decrypt(
            __import__("base64").b64decode(encoded),
            padding.OAEP(
                mgf=padding.MGF1(algorithm=hashes.SHA256()),
                algorithm=hashes.SHA256(),
                label=None,
            ),
        )
        self.assertEqual(__import__("json").loads(plaintext)["irk"], "AA" * 16)


if __name__ == "__main__":
    unittest.main()
