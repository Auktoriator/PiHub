"""Password, sessions and the brake against guessing.

The password is never stored, only a scrypt hash with its own random salt.
Sessions live in the memory of the runner only: a restart logs everybody out.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import math
import os
import secrets
import tempfile
import time
from pathlib import Path

MIN_LENGTH = 8
SCRYPT = {"n": 2**14, "r": 8, "p": 1, "dklen": 32}
SCRYPT_MAXMEM = 128 * 1024 * 1024
SESSION_SECONDS = 12 * 3600
FREE_TRIES = 5
LOCK_FIRST = 60
LOCK_MAX = 900


class PasswordError(Exception):
    """The password cannot be used. `key` names the reason."""

    def __init__(self, key: str):
        super().__init__(key)
        self.key = key


def private_dir(data_dir: Path) -> Path:
    """<data>/private: owner only. The password hash and the log live here,
    out of sight of the web page (which sees the rest of <data>)."""
    path = Path(data_dir) / "private"
    path.mkdir(mode=0o700, parents=True, exist_ok=True)
    os.chmod(path, 0o700)
    return path


def login_file(data_dir: Path) -> Path:
    return Path(data_dir) / "private" / "login.json"


_file = login_file


def write_private(path: Path, text: str) -> None:
    """Write a file readable by the owner only, replacing it in one step."""
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=path.name + ".")
    try:
        with os.fdopen(fd, "w") as f:
            os.fchmod(f.fileno(), 0o600)
            f.write(text)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def _hash(password: str, salt: bytes, params: dict) -> bytes:
    return hashlib.scrypt(password.encode(), salt=salt, n=params["n"], r=params["r"], p=params["p"],
                          dklen=params["dklen"], maxmem=SCRYPT_MAXMEM)


def set_password(data_dir: Path, password: str) -> None:
    if not isinstance(password, str) or len(password) < MIN_LENGTH:
        raise PasswordError("password_short")
    private_dir(data_dir)
    salt = secrets.token_bytes(16)
    entry = {"scheme": "scrypt", **SCRYPT, "salt": salt.hex(), "hash": _hash(password, salt, SCRYPT).hex()}
    write_private(_file(data_dir), json.dumps(entry, indent=1) + "\n")


def password_is_set(data_dir: Path) -> bool:
    try:
        entry = json.loads(_file(data_dir).read_text())
        return entry.get("scheme") == "scrypt" and bool(entry.get("hash")) and bool(entry.get("salt"))
    except (OSError, ValueError, AttributeError):
        return False


def verify(data_dir: Path, password) -> bool:
    if not isinstance(password, str):
        return False
    try:
        entry = json.loads(_file(data_dir).read_text())
        params = {k: entry[k] for k in ("n", "r", "p", "dklen")}
        if not all(isinstance(v, int) for v in params.values()) or params["n"] > 2**16 or params["n"] < 2:
            return False
        expected = bytes.fromhex(entry["hash"])
        got = _hash(password, bytes.fromhex(entry["salt"]), params)
    except (OSError, ValueError, KeyError, TypeError, AttributeError):
        return False
    return hmac.compare_digest(got, expected)


class Sessions:
    """Random tokens, valid for 12 hours, kept in memory only."""

    def __init__(self, clock=time.time, lifetime: int = SESSION_SECONDS):
        self.clock, self.lifetime = clock, lifetime
        self._until: dict[str, float] = {}  # sha256 of the token -> expiry

    @staticmethod
    def _key(token) -> str:
        return hashlib.sha256(str(token).encode()).hexdigest()

    def _purge(self) -> None:
        now = self.clock()
        for key in [k for k, until in self._until.items() if until <= now]:
            del self._until[key]

    def login(self) -> str:
        self._purge()
        token = secrets.token_urlsafe(32)
        self._until[self._key(token)] = self.clock() + self.lifetime
        return token

    def valid(self, token) -> bool:
        self._purge()
        return isinstance(token, str) and self._key(token) in self._until

    def clear(self) -> None:
        self._until.clear()

    def logout(self, token) -> None:
        self._until.pop(self._key(token), None)


class Throttle:
    """Five wrong tries in a row lock the login for 60 s; every further
    series of five doubles the lock, up to 15 minutes. A right password
    resets it. It counts for the whole machine, not per client address: an
    address is not a thing to trust here, and the owner can wait."""

    def __init__(self, clock=time.monotonic):
        self.clock = clock
        self.failures = 0
        self.locked_until = 0.0

    def remaining(self) -> int:
        """Seconds of lock left, 0 when a try is allowed."""
        left = self.locked_until - self.clock()
        return math.ceil(left) if left > 0 else 0

    def failed(self) -> None:
        self.failures += 1
        if self.failures % FREE_TRIES == 0:
            series = self.failures // FREE_TRIES
            self.locked_until = self.clock() + min(LOCK_FIRST * 2 ** (series - 1), LOCK_MAX)

    def succeeded(self) -> None:
        self.failures = 0
        self.locked_until = 0.0
