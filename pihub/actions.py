"""Actions: the commands a card allows, and the log of what was run.

Everything here runs in the runner, never in the web process. The command
always comes from the card on disk, read fresh at the moment of the request.
The only thing a caller can add is one value, and only from a list that is
measured again right now.
"""
from __future__ import annotations

import json
import os
import re
import shlex
import signal
import subprocess
import threading
import time
from collections import deque
from pathlib import Path

from . import auth
from . import cards as cardlib
from . import scan

OUTPUT_MAX = 20_000
KILL_GRACE = 5  # seconds between SIGTERM and SIGKILL
VALUE_PATTERN = re.compile(r"[A-Za-z0-9_.:/@-]{1,200}")  # used with fullmatch: "$" would let a trailing newline through
LOG_KEEP = 5000  # entries kept when the log is cut back
LOG_CUT_AT = 5500  # lines in the file that trigger the cut
_log_lines: dict[str, int] = {}  # line count per log file, to avoid counting on every write


def value_ok(value) -> bool:
    """A plain word that cannot be taken for an option: no leading "-"."""
    return isinstance(value, str) and bool(VALUE_PATTERN.fullmatch(value)) and not value.startswith("-")
LOG_LIMIT_MAX = 1000
_log_lock = threading.Lock()


class ActionError(Exception):
    """A request that must not run. `key` is unknown_action, level_copy or bad_value."""

    def __init__(self, key: str):
        super().__init__(key)
        self.key = key


# --- sources of values ------------------------------------------------------

def blocked_addresses() -> list:
    try:
        rows = scan.blocks()["decisions"]
    except (scan.Unavailable, OSError, ValueError, KeyError, TypeError):
        return []
    return [r.get("value") for r in rows if isinstance(r, dict)]


def unmounted_targets() -> list:
    try:
        rows = scan.storage()["planned"]
    except (scan.Unavailable, OSError, ValueError, KeyError, TypeError):
        return []
    return [r.get("target") for r in rows if isinstance(r, dict) and r.get("mounted") is False]


CHOICES = {"blocked_addresses": blocked_addresses, "unmounted_targets": unmounted_targets}


def allowed_values(source: str, choices: dict | None = None) -> list:
    """Measure now which values are allowed. Anything that could not be a
    plain word is dropped here already, so it is never offered."""
    fn = (CHOICES if choices is None else choices).get(source)
    if fn is None:
        return []
    seen, out = set(), []
    for v in fn():
        if value_ok(v) and v not in seen:
            seen.add(v)
            out.append(v)
    return out


# --- from request to command ------------------------------------------------

def _find(data_dir: Path, card_id, index) -> dict:
    if not isinstance(card_id, str) or not isinstance(index, int) or isinstance(index, bool) or index < 0:
        raise ActionError("unknown_action")
    card = cardlib.load(Path(data_dir))[1].get(card_id)
    actions = card.get("actions") if card else None
    if not isinstance(actions, list) or index >= len(actions) or not isinstance(actions[index], dict):
        raise ActionError("unknown_action")
    action = actions[index]
    if not isinstance(action.get("run"), str) or not action["run"].strip() or not isinstance(action.get("label"), str):
        raise ActionError("unknown_action")
    if action.get("level") not in cardlib.LEVELS:
        raise ActionError("unknown_action")
    if action["level"] == "copy":
        raise ActionError("level_copy")  # enforced here, not only by the page
    return action


def _timeout(action: dict) -> int:
    t = action.get("timeout", cardlib.TIMEOUT_DEFAULT)
    ok = isinstance(t, int) and not isinstance(t, bool) and 1 <= t <= cardlib.TIMEOUT_MAX
    return t if ok else cardlib.TIMEOUT_DEFAULT


def resolve(data_dir: Path, card_id, index, value=None, choices: dict | None = None) -> dict:
    """Turn (card, action number, value) into the command to run."""
    action = _find(data_dir, card_id, index)
    run, source = action["run"], action.get("choose")
    if source is None:
        if "{value}" in run:
            raise ActionError("unknown_action")
        if value not in (None, ""):
            raise ActionError("bad_value")
        command = run
    else:
        if source not in cardlib.CHOICE_SOURCES or run.count("{value}") != 1:
            raise ActionError("unknown_action")
        if not value_ok(value):
            raise ActionError("bad_value")
        if value not in allowed_values(source, choices):
            raise ActionError("bad_value")
        command = run.replace("{value}", shlex.quote(value))
    return {"command": command, "label": action["label"], "level": action["level"], "timeout": _timeout(action)}


def choices_for(data_dir: Path, card_id, index, choices: dict | None = None) -> list:
    action = _find(data_dir, card_id, index)
    source = action.get("choose")
    return allowed_values(source, choices) if source in cardlib.CHOICE_SOURCES else []


# --- running ----------------------------------------------------------------

def _killpg(pgid: int, sig: int) -> None:
    try:
        os.killpg(pgid, sig)
    except (ProcessLookupError, PermissionError):
        pass


def execute(command: str, timeout: int) -> dict:
    """Run `command` with bash, stdin closed, stdout and stderr together,
    in its own process group. On timeout the whole group is ended."""
    started = time.monotonic()
    tail, state = bytearray(), {"cut": False}
    cap = OUTPUT_MAX * 4  # bytes: a character is at most four

    proc = subprocess.Popen(["bash", "-c", command], stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                            stderr=subprocess.STDOUT, start_new_session=True)

    def drain():
        try:
            for chunk in iter(lambda: proc.stdout.read(8192), b""):
                tail.extend(chunk)
                if len(tail) > cap:
                    del tail[:len(tail) - cap]
                    state["cut"] = True
        finally:
            proc.stdout.close()  # here, not in the caller: closing under a blocked read would wait for it

    reader = threading.Thread(target=drain, daemon=True)
    reader.start()
    timed_out = False
    try:
        proc.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        timed_out = True
        _killpg(proc.pid, signal.SIGTERM)
        try:
            proc.wait(timeout=KILL_GRACE)
        except subprocess.TimeoutExpired:
            pass
        _killpg(proc.pid, signal.SIGKILL)  # also anything the leader left behind
        proc.wait()
    reader.join(2)  # a daemon that kept the pipe open must not hold the answer back
    text = bytes(tail).decode("utf-8", errors="replace")
    truncated = state["cut"] or len(text) > OUTPUT_MAX
    return {"exit": None if timed_out else proc.returncode, "timed_out": timed_out,
            "seconds": round(time.monotonic() - started, 1),
            "output": text[-OUTPUT_MAX:], "truncated": truncated}


# --- log --------------------------------------------------------------------

def _log_file(data_dir: Path) -> Path:
    return Path(data_dir) / "private" / "actions.log"


def now() -> str:
    from datetime import datetime
    return datetime.now().astimezone().isoformat(timespec="seconds")


def log_append(data_dir: Path, entry: dict) -> None:
    path = _log_file(data_dir)
    auth.private_dir(data_dir)
    line = (json.dumps(entry, ensure_ascii=False) + "\n").encode()
    with _log_lock:
        fd = os.open(path, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)
        try:
            os.write(fd, line)
        finally:
            os.close(fd)
        key = str(path)
        if key not in _log_lines:
            _log_lines[key] = path.read_bytes().count(b"\n")
        else:
            _log_lines[key] += 1
        if _log_lines[key] > LOG_CUT_AT:
            kept = path.read_bytes().splitlines(keepends=True)[-LOG_KEEP:]
            auth.write_private(path, b"".join(kept).decode("utf-8", errors="replace"))
            _log_lines[key] = len(kept)


def log_read(data_dir: Path, limit: int = 100) -> list:
    limit = max(1, min(int(limit), LOG_LIMIT_MAX))
    try:
        with open(_log_file(data_dir), "rb") as f:
            size = f.seek(0, os.SEEK_END)
            f.seek(max(0, size - 4_000_000))
            lines = deque(f.read().splitlines(), maxlen=limit + 1)
    except OSError:
        return []
    out = []
    for raw in reversed(lines):
        try:
            entry = json.loads(raw)
        except ValueError:
            continue  # a cut-off first line or a damaged one
        if isinstance(entry, dict):
            out.append(entry)
        if len(out) == limit:
            break
    return out
