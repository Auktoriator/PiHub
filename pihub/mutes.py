"""Findings the owner has silenced on purpose, each with a reason."""
from __future__ import annotations

import json
import time
from pathlib import Path

from .auth import write_private

KEY_MAX = 200
REASON_MIN, REASON_MAX = 3, 300


class MuteError(Exception):
    """`key` is bad_key or bad_reason."""

    def __init__(self, key: str):
        super().__init__(key)
        self.key = key


def _file(data_dir: Path) -> Path:
    return Path(data_dir) / "muted.json"


def load(data_dir: Path) -> dict:
    try:
        data = json.loads(_file(data_dir).read_text())
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _save(data_dir: Path, data: dict) -> None:
    write_private(_file(data_dir), json.dumps(data, indent=1, ensure_ascii=False) + "\n")


def mute(data_dir: Path, key, reason) -> dict:
    if not isinstance(key, str) or not 1 <= len(key) <= KEY_MAX:
        raise MuteError("bad_key")
    reason = reason.strip() if isinstance(reason, str) else ""
    if not REASON_MIN <= len(reason) <= REASON_MAX:
        raise MuteError("bad_reason")
    data = load(data_dir)
    data[key] = {"reason": reason, "since": time.strftime("%Y-%m-%dT%H:%M:%S%z")}
    _save(data_dir, data)
    return data


def unmute(data_dir: Path, key) -> dict:
    if not isinstance(key, str) or not 1 <= len(key) <= KEY_MAX:
        raise MuteError("bad_key")
    data = load(data_dir)
    if data.pop(key, None) is not None:
        _save(data_dir, data)
    return data
