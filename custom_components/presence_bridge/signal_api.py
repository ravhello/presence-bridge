"""Revocable, per-identity phone signal API. Never a Home Assistant bearer."""

import base64
import io
import json
import time
from pathlib import Path
from urllib.parse import urlencode

import segno
from aiohttp import web
from cryptography import x509
from cryptography.hazmat.primitives import hashes
from homeassistant.components.http import HomeAssistantView

from .signal_access import matches_access, monitor_origin, new_access

HEADERS = {"Cache-Control": "no-store", "Referrer-Policy": "no-referrer"}


def certificate_fingerprint(config_path: str) -> str | None:
    """Public certificate only; no access to the server's private key."""
    try:
        data = json.loads(Path(config_path).read_text(encoding="utf-8"))
        path = data["data"]["stable"].get("ssl_certificate")
        if path:
            certificate = x509.load_pem_x509_certificate(Path(path).read_bytes())
            return certificate.fingerprint(hashes.SHA256()).hex()
    except (OSError, ValueError, KeyError, TypeError):
        pass
    return None


async def create_monitor(coordinator, identity_id: str, origin: str) -> dict:
    origin = monitor_origin(origin)
    row = coordinator.memory["identities"][identity_id]
    token, access = new_access(600)
    row["monitor_pending"] = access
    await coordinator.store.async_save(coordinator.memory)
    fingerprint = await coordinator.hass.async_add_executor_job(
        certificate_fingerprint, coordinator.hass.config.path(".storage", "http")
    )
    query = {
        "v": 1,
        "url": origin + "/api/presence_bridge/signal",
        "token": token,
        "exp": access["expires"],
    }
    if fingerprint:
        query["pin"] = fingerprint
    uri = "presencepair://monitor?" + urlencode(query)
    output = io.BytesIO()
    segno.make(uri, error="m").save(output, kind="png", scale=6, border=4)
    return {
        "uri": uri,
        "qr_data_uri": "data:image/png;base64,"
        + base64.b64encode(output.getvalue()).decode(),
        "expires_at": access["expires"],
    }


class SignalView(HomeAssistantView):
    url = "/api/presence_bridge/signal"
    name = "api:presence_bridge:signal"
    requires_auth = False

    def __init__(self, get_coordinator):
        self.get_coordinator = get_coordinator
        self.last_reads = {}

    def authorized(self, request, field):
        token = request.headers.get("X-Presence-Monitor", "")
        try:
            c = self.get_coordinator(request.app["hass"])
        except RuntimeError:
            raise web.HTTPServiceUnavailable(headers=HEADERS) from None
        for identity_id, row in c.memory.get("identities", {}).items():
            if identity_id in c.identity_states and matches_access(
                token, row.get(field)
            ):
                return c, identity_id, row
        raise web.HTTPUnauthorized(headers=HEADERS)

    async def get(self, request):
        c, identity_id, _row = self.authorized(request, "monitor_access")
        now = time.monotonic()
        key = (c.entry.entry_id, identity_id)
        if now - self.last_reads.get(key, 0) < 1:
            raise web.HTTPTooManyRequests(headers={**HEADERS, "Retry-After": "2"})
        self.last_reads = {k: t for k, t in self.last_reads.items() if now - t < 60}
        self.last_reads[key] = now
        return web.json_response(c.signal_payload(identity_id), headers=HEADERS)

    async def post(self, request):
        # One-time QR redemption. A later QR does not revoke the current app
        # until redeemed. Deleting the identity removes both credentials.
        c, _identity_id, row = self.authorized(request, "monitor_pending")
        token, access = new_access(90 * 86400)
        row.pop("monitor_pending", None)
        row["monitor_access"] = access
        await c.store.async_save(c.memory)
        return web.json_response(
            {"token": token, "expires_at": access["expires"]}, headers=HEADERS
        )
