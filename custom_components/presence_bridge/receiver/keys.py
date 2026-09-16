"""Read only the selected BlueZ peer, never enumerate other devices' keys."""

from __future__ import annotations

import configparser
import hashlib
import os
import re
import stat
from dataclasses import dataclass, field
from pathlib import Path

MAC = re.compile(r"^[0-9A-F]{2}(?::[0-9A-F]{2}){5}$")
HEX_KEY = re.compile(r"^[0-9A-Fa-f]{32}$")


class ReceiverError(Exception):
    """A deliberately non-secret, user-actionable error."""


def address(value: str) -> str:
    value = str(value).upper()
    if not MAC.fullmatch(value):
        raise ReceiverError("Invalid Bluetooth address")
    return value


@dataclass(frozen=True)
class Bond:
    peer: str = field(repr=False)
    irk: str = field(repr=False)

    @property
    def fingerprint(self) -> str:
        return hashlib.sha256(bytes.fromhex(self.irk)).hexdigest()


class BlueZKeys:
    """BlueZ keeps identity material little-endian; HA expects AES byte order."""

    def __init__(self, root: Path, adapter: str):
        self.root = root
        self.adapter = address(adapter)

    def check_access(self) -> None:
        directory = self.root / self.adapter
        if not directory.is_dir() or not os.access(directory, os.R_OK | os.X_OK):
            raise ReceiverError(
                "BlueZ bond storage is not readable. See Linux setup: mount the "
                "selected adapter directory read-only, or use a Linux/Windows receiver."
            )

    def read(self, peer: str) -> Bond:
        peer = address(peer)
        path = self.root / self.adapter / peer / "info"
        # Reject symlinks, including parent directories; never follow a supplied path.
        for part in (self.root, self.root / self.adapter, path.parent, path):
            if part.is_symlink():
                raise ReceiverError("BlueZ key paths must not be symbolic links")
        try:
            with path.open("r", encoding="utf-8") as handle:
                info = os.fstat(handle.fileno())
                if not stat.S_ISREG(info.st_mode) or info.st_size > 65536:
                    raise ReceiverError("Invalid BlueZ bond file")
                parser = configparser.ConfigParser(interpolation=None, strict=True)
                parser.read_string(handle.read(65537))
            key = parser.get("IdentityResolvingKey", "Key")
            ltk = parser.get("LongTermKey", "Key")
            authenticated = parser.getint("LongTermKey", "Authenticated")
            size = parser.getint("LongTermKey", "EncSize")
            if not HEX_KEY.fullmatch(key) or not HEX_KEY.fullmatch(ltk):
                raise ValueError
            if authenticated != 1 or size != 16:
                raise ReceiverError(
                    "Bluetooth bond is not authenticated with a 128-bit key"
                )
            return Bond(peer, bytes.fromhex(key)[::-1].hex().upper())
        except (OSError, configparser.Error, ValueError) as err:
            raise ReceiverError(
                "Authenticated BlueZ identity is not available yet"
            ) from err
