"""Pin the public HTTP certificate without reading HA storage or private keys."""

import ast
import asyncio
import base64
import io
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock
from urllib.parse import parse_qs, urlencode, urlsplit

import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.serialization import Encoding
from cryptography.x509.oid import NameOID
from test_signal_access import access

SOURCE = Path(__file__).parents[1] / "custom_components/presence_bridge/signal_api.py"


def monitor_functions():
    tree = ast.parse(SOURCE.read_text(encoding="utf-8"))
    namespace = {
        "Path": Path,
        "x509": x509,
        "hashes": hashes,
        "base64": base64,
        "io": io,
        "urlencode": urlencode,
        "monitor_origin": access.monitor_origin,
        "new_access": access.new_access,
        "segno": SimpleNamespace(
            make=Mock(
                return_value=SimpleNamespace(
                    save=lambda output, **_: output.write(b"test-qr")
                )
            )
        ),
    }
    functions = [
        node
        for node in tree.body
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef)
    ]
    exec(
        compile(ast.Module(body=functions, type_ignores=[]), str(SOURCE), "exec"),
        namespace,
    )
    return namespace


@pytest.fixture
def public_certificate(tmp_path):
    key = ec.generate_private_key(ec.SECP256R1())
    name = x509.Name(
        [x509.NameAttribute(NameOID.COMMON_NAME, "test.homeassistant.local")]
    )
    now = datetime.now(UTC)
    certificate = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(minutes=1))
        .not_valid_after(now + timedelta(days=1))
        .sign(key, hashes.SHA256())
    )
    path = tmp_path / "public-certificate.pem"
    path.write_bytes(certificate.public_bytes(Encoding.PEM))
    return path, certificate.fingerprint(hashes.SHA256()).hex()


def test_fingerprint_reads_the_certificate_directly(public_certificate):
    path, expected = public_certificate
    assert monitor_functions()["certificate_fingerprint"](str(path)) == expected


@pytest.mark.parametrize("kind", ["missing", "invalid", "directory", "disabled"])
def test_missing_or_invalid_certificate_has_no_pin_or_storage_fallback(tmp_path, kind):
    path = tmp_path / "certificate.pem"
    if kind == "invalid":
        path.write_text("not a certificate", encoding="utf-8")
    elif kind == "directory":
        path.mkdir()
    value = None if kind == "disabled" else str(path)
    assert monitor_functions()["certificate_fingerprint"](value) is None


@pytest.mark.parametrize("mode", ["https", "reverse_proxy", "attribute_unavailable"])
def test_monitor_uses_public_http_api_and_never_accesses_storage(
    public_certificate, mode
):
    path, fingerprint = public_certificate
    ns = monitor_functions()
    private_config = Mock(side_effect=AssertionError("HA storage must not be read"))
    http = SimpleNamespace(ssl_certificate=str(path) if mode == "https" else None)
    if mode == "attribute_unavailable":
        http = object()
    coordinator = SimpleNamespace(
        hass=SimpleNamespace(
            http=http,
            config=SimpleNamespace(path=private_config),
            async_add_executor_job=AsyncMock(side_effect=asyncio.to_thread),
        ),
        memory={"identities": {"phone": {"monitor_access": {"existing": "preserve"}}}},
        store=SimpleNamespace(async_save=AsyncMock()),
    )
    result = asyncio.run(
        ns["create_monitor"](coordinator, "phone", "https://homeassistant.local:8123")
    )
    query = parse_qs(urlsplit(result["uri"]).query)
    assert query["url"] == [
        "https://homeassistant.local:8123/api/presence_bridge/signal"
    ]
    assert query.get("pin") == ([fingerprint] if mode == "https" else None)
    row = coordinator.memory["identities"]["phone"]
    assert access.matches_access(query["token"][0], row["monitor_pending"])
    assert query["token"][0] not in str(coordinator.memory)
    assert row["monitor_access"] == {"existing": "preserve"}
    coordinator.store.async_save.assert_awaited_once()
    private_config.assert_not_called()
    coordinator.hass.async_add_executor_job.assert_awaited_once_with(
        ns["certificate_fingerprint"], str(path) if mode == "https" else None
    )
    assert ".storage" not in SOURCE.read_text(encoding="utf-8")


def test_real_ha_public_http_certificate_and_person_states(
    tmp_path, public_certificate
):
    pytest.importorskip(
        "homeassistant", reason="Runs with real HA in the permissions job"
    )
    from homeassistant.components.http import HomeAssistantHTTP
    from homeassistant.core import HomeAssistant
    from test_person_link import person_link_status

    async def exercise():
        hass = HomeAssistant(str(tmp_path))
        try:
            # Use the actual HTTP API object without opening a listener.
            http = object.__new__(HomeAssistantHTTP)
            path, fingerprint = public_certificate
            http.ssl_certificate = str(path)
            hass.http = http
            coordinator = SimpleNamespace(
                hass=hass,
                memory={"identities": {"phone": {}}},
                store=SimpleNamespace(async_save=AsyncMock()),
            )
            result = await monitor_functions()["create_monitor"](
                coordinator, "phone", "https://homeassistant.local:8123"
            )
            assert parse_qs(urlsplit(result["uri"]).query)["pin"] == [fingerprint]
            hass.states.async_set(
                "person.test",
                "home",
                {"editable": True, "device_trackers": ["device_tracker.gps"]},
            )
            assert (
                person_link_status(hass, "person.test", "device_tracker.ble")
                == "manual_configuration_required"
            )
            hass.states.async_set(
                "person.test",
                "home",
                {
                    "editable": True,
                    "device_trackers": ["device_tracker.gps", "device_tracker.ble"],
                },
            )
            assert (
                person_link_status(hass, "person.test", "device_tracker.ble")
                == "linked"
            )
            hass.states.async_set(
                "person.test",
                "home",
                {"editable": True, "device_trackers": ["device_tracker.gps"]},
            )
            assert (
                person_link_status(hass, "person.test", "device_tracker.ble")
                == "manual_configuration_required"
            )
        finally:
            await hass.async_block_till_done()
            await hass.async_stop(force=True)

    asyncio.run(exercise())
