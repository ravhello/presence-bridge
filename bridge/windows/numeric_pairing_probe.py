"""Authenticated QR-scoped Windows pairing and optional clean-room diagnostics."""

from __future__ import annotations

import asyncio
import json
import logging
import re
import secrets
import sys
import time
from collections.abc import Awaitable, Callable
from contextlib import suppress
from pathlib import Path
from typing import Any

from protocol import PairingLink
from reverse_gatt_client import PairingDiagnosticError

if sys.platform == "win32":
    from winrt.windows.devices.enumeration import (
        DeviceInformation,
        DevicePairingKinds,
        DevicePairingProtectionLevel,
        DevicePairingResultStatus,
    )
else:
    DeviceInformation = None
    DevicePairingKinds = None
    DevicePairingProtectionLevel = None
    DevicePairingResultStatus = None

LOGGER = logging.getLogger("presence_bridge.numeric_pairing_probe")
ARM_NAME = "numeric-comparison-arm.json"
REQUEST_NAME = "numeric-comparison-request.json"
RESPONSE_NAME = "numeric-comparison-response.json"


def _fail(message: str, code: str) -> PairingDiagnosticError:
    return PairingDiagnosticError(message, code)


async def pair_with_numeric_comparison(
    client: Any,
    confirm: Callable[[str, float], Awaitable[bool]],
    deadline: float,
    *,
    allow_authenticated_bond: bool = False,
) -> bool:
    """No Just Works fallback or unpair; consent is supplied by the caller."""
    if DeviceInformation is None:
        raise _fail("Numeric comparison requires WinRT", "numeric_pairing_unavailable")
    deadline = min(deadline, time.monotonic() + 90)
    tasks: set[asyncio.Task[None]] = set()
    active = True
    confirmed = False
    loop = asyncio.get_running_loop()
    try:
        async with asyncio.timeout_at(deadline):
            requester = client._backend._requester
            info = await DeviceInformation.create_from_id_async(
                requester.device_information.id
            )
            if info.pairing.is_paired:
                if allow_authenticated_bond:
                    if info.pairing.protection_level == (
                        DevicePairingProtectionLevel.ENCRYPTION_AND_AUTHENTICATION
                    ):
                        # The caller must still complete the protected GATT ACK.
                        return True
                    raise _fail(
                        "The saved bond lacks authenticated encryption. Remove this "
                        "phone in Presence Bridge and forget the receiver on iPhone, "
                        "then retry. No other device has been changed.",
                        "numeric_pairing_saved_bond_weak",
                    )
                raise _fail(
                    "This fresh-pairing experiment requires the previous bond to "
                    "be removed first; no device was unpaired automatically",
                    "numeric_pairing_existing_bond",
                )
            if not info.pairing.can_pair:
                raise _fail(
                    "Windows cannot pair this peer", "numeric_pairing_unavailable"
                )
            custom = info.pairing.custom

            async def approve(args: Any, deferral: Any) -> None:
                nonlocal confirmed
                try:
                    pin = str(args.pin)
                    if (
                        args.pairing_kind == DevicePairingKinds.CONFIRM_PIN_MATCH
                        and re.fullmatch(r"[0-9]{6}", pin)
                        and await confirm(pin, deadline)
                        and active
                        and time.monotonic() < deadline
                    ):
                        args.accept()
                        confirmed = True
                except asyncio.CancelledError:
                    raise
                except Exception as error:
                    # Never put the comparison PIN or raw native payload in logs.
                    LOGGER.warning(
                        "Comparison confirmation failed: %s", type(error).__name__
                    )
                finally:
                    deferral.complete()

            def schedule(args: Any, deferral: Any) -> None:
                if not active:
                    deferral.complete()
                    return
                task = asyncio.create_task(approve(args, deferral))
                tasks.add(task)

            def requested(_sender: Any, args: Any) -> None:
                deferral = args.get_deferral()
                loop.call_soon_threadsafe(schedule, args, deferral)

            token = custom.add_pairing_requested(requested)
            try:
                result = await custom.pair_with_protection_level_async(
                    DevicePairingKinds.CONFIRM_PIN_MATCH,
                    DevicePairingProtectionLevel.ENCRYPTION_AND_AUTHENTICATION,
                )
            finally:
                active = False
                custom.remove_pairing_requested(token)
                for task in tasks:
                    if not task.done():
                        task.cancel()
                for task in tasks:
                    with suppress(asyncio.CancelledError):
                        await task
            # WinRT can return stale ProtectionLevelUsed (including NONE) after
            # successful pairing. Re-enumerate this exact peer before deciding.
            reported_level = result.protection_level_used
            verified_level = None
            verified_paired = False
            if result.status == DevicePairingResultStatus.PAIRED and confirmed:
                refreshed = await DeviceInformation.create_from_id_async(
                    requester.device_information.id
                )
                verified_paired = refreshed.pairing.is_paired
                verified_level = refreshed.pairing.protection_level
            LOGGER.info(
                "Numeric comparison result=%s reported_protection=%s "
                "verified_protection=%s verified_paired=%s confirmed=%s",
                result.status.name,
                reported_level.name,
                verified_level.name if verified_level is not None else "UNVERIFIED",
                verified_paired,
                confirmed,
            )
            if not (
                result.status == DevicePairingResultStatus.PAIRED
                and verified_paired
                and verified_level
                == DevicePairingProtectionLevel.ENCRYPTION_AND_AUTHENTICATION
                and confirmed
            ):
                raise _fail(
                    "Windows numeric comparison did not establish authenticated "
                    f"encryption (status={result.status.name}, "
                    f"reported_protection={reported_level.name}, "
                    f"verified_protection={verified_level.name if verified_level is not None else 'UNVERIFIED'}, "
                    f"paired={verified_paired}, confirmed={confirmed})",
                    "numeric_pairing_not_authenticated",
                )
            return False
    except PairingDiagnosticError:
        raise
    except asyncio.CancelledError:
        raise
    except TimeoutError as error:
        raise _fail(
            "Numeric comparison timed out", "numeric_pairing_timeout"
        ) from error
    except Exception as error:
        raise _fail(
            f"Numeric comparison failed ({type(error).__name__})",
            "numeric_pairing_native_error",
        ) from error


class QRSessionPairing:
    """Receiver consent after HMAC proof, without an operator or local arm file.

    Only ReverseGattPairer calls this, after matching the active session and
    verifying its claim. A native bond alone is never enrollment success.
    """

    def __init__(self, directory, write_json, progress) -> None:
        self.diagnostic = NumericPairingProbe(directory, write_json, progress)
        self.progress = progress
        self.reused_bond = False

    async def __call__(self, client: Any, link: PairingLink, deadline: float) -> bool:
        self.reused_bond = False
        if await self.diagnostic(client, link, deadline):
            return True
        self.progress(
            "numeric_comparison_starting",
            "QR verified; preparing authenticated Bluetooth pairing",
        )

        async def confirm(_pin: str, native_deadline: float) -> bool:
            self.progress(
                "numeric_comparison_receiver_consented",
                "Receiver ready; accept Pair on the iPhone",
            )
            return time.monotonic() < native_deadline

        self.reused_bond = await pair_with_numeric_comparison(
            client, confirm, deadline, allow_authenticated_bond=True
        )
        self.progress(
            "numeric_comparison_verified",
            "Bluetooth security verified; confirming the protected app exchange",
        )
        return True


class NumericPairingProbe:
    """Consume an exact-session arm with manual or explicit owner consent."""

    def __init__(
        self,
        directory: Path,
        write_json: Callable[[Path, dict[str, Any]], None],
        progress: Callable[[str, str], None],
    ) -> None:
        self.directory = directory
        self.write_json = write_json
        self.progress = progress
        self.used = False

    async def __call__(self, client: Any, link: PairingLink, deadline: float) -> bool:
        arm_path = self.directory / ARM_NAME
        if self.used:
            raise _fail(
                "The diagnostic was already used", "numeric_pairing_already_used"
            )
        if not arm_path.is_file():
            return False
        try:
            arm = json.loads(arm_path.read_text(encoding="utf-8-sig"))
            if arm.get("session_id") != link.session_id:
                return False
            if arm.get("observer_id") != link.observer_id:
                raise ValueError("Observer mismatch")
            approval_mode = arm.get("approval_mode", "compare")
            if approval_mode not in {"compare", "qr_authorized"}:
                raise ValueError("Invalid receiver consent mode")
            if arm.get("state", "armed") != "armed":
                raise _fail(
                    "The diagnostic was already consumed for this QR session",
                    "numeric_pairing_already_used",
                )
            remaining = float(arm["expires_at"]) - time.time()
            if not 0 < remaining <= 600:
                raise ValueError("Expired diagnostic")
        except (OSError, ValueError, KeyError, TypeError) as error:
            raise _fail(
                "Invalid numeric pairing test arm", "numeric_pairing_invalid_arm"
            ) from error
        self.used = True
        # Keep the consumed marker so a helper/HA restart cannot silently retry
        # this same experiment with the normal, weaker pairing ceremony.
        self.write_json(arm_path, {**arm, "state": "consumed"})
        LOGGER.info("One-session receiver consent mode=%s", approval_mode)
        self.progress(
            "numeric_comparison_starting",
            "Diagnostic: requesting an authenticated Bluetooth bond with PIN comparison",
        )

        async def confirm(pin: str, native_deadline: float) -> bool:
            if approval_mode == "qr_authorized":
                # The owner explicitly preauthorized this exact QR session.
                # The reverse client calls this probe only after QR/HMAC proof.
                # This is receiver consent, not a claimed visual PIN comparison.
                self.progress(
                    "numeric_comparison_receiver_consented",
                    "Receiver consent granted for the QR-verified app; accept Pair on the iPhone",
                )
                return time.monotonic() < native_deadline
            return await self.confirm(link.session_id, pin, native_deadline)

        await pair_with_numeric_comparison(client, confirm, deadline)
        self.progress(
            "numeric_comparison_verified",
            "Bluetooth numeric comparison accepted; verifying the protected app acknowledgement",
        )
        return True

    async def confirm(self, session_id: str, pin: str, deadline: float) -> bool:
        request_path = self.directory / REQUEST_NAME
        response_path = self.directory / RESPONSE_NAME
        nonce = secrets.token_urlsafe(24)
        deadline = min(deadline, time.monotonic() + 60)
        response_path.unlink(missing_ok=True)
        request = {
            "schema": 1,
            "session_id": session_id,
            "nonce": nonce,
            "pin": pin,
            "expires_at": time.time() + max(0, deadline - time.monotonic()),
        }
        self.write_json(request_path, request)
        self.progress(
            "numeric_comparison_confirmation_required",
            "Compare the Bluetooth code on the iPhone and confirm it with the test operator",
        )
        try:
            while time.monotonic() < deadline:
                try:
                    reply = json.loads(response_path.read_text(encoding="utf-8-sig"))
                except (OSError, ValueError):
                    reply = {}
                if (
                    reply.get("session_id") == session_id
                    and reply.get("nonce") == nonce
                    and type(reply.get("approved")) is bool
                    and time.monotonic() < deadline
                ):
                    return reply["approved"]
                await asyncio.sleep(0.2)
            return False
        finally:
            request_path.unlink(missing_ok=True)
            response_path.unlink(missing_ok=True)
