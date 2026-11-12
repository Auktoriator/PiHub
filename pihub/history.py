"""History of readings and card status changes, kept as JSON lines.

`history.jsonl` holds one measurement per collection run (14 days kept),
`changes.jsonl` one line per card whose status changed (last 500 kept).
"""
from __future__ import annotations

import json
import os
import time
from pathlib import Path

from . import scan

HISTORY_FILE = "history.jsonl"
CHANGES_FILE = "changes.jsonl"
KEEP_SECONDS = 14 * 86400
HISTORY_LIMIT = 4032  # 14 days at one run per 5 minutes
CHANGES_LIMIT = 500
SLACK = 1.1  # rewrite only when the file is a tenth over its limit
MAX_POINTS = 300
VIEW_CHANGES = 50


def _read(path: Path) -> list[dict]:
    rows = []
    try:
        with open(path, encoding="utf-8") as f:
            for line in f:
                try:
                    row = json.loads(line)
                except ValueError:
                    continue
                if isinstance(row, dict):
                    rows.append(row)
    except OSError:
        pass
    return rows


def _append(path: Path, rows: list[dict]) -> None:
    if not rows:
        return
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
    with os.fdopen(fd, "a", encoding="utf-8") as f:
        f.write("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows))
    os.chmod(path, 0o600)


def _rewrite(path: Path, rows: list[dict]) -> None:
    tmp = path.with_suffix(".tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows))
    os.replace(tmp, path)


def _line_count(path: Path) -> int:
    try:
        with open(path, "rb") as f:
            return sum(1 for _ in f)
    except OSError:
        return 0


def _trim(data_dir: Path, now: int) -> None:
    hist = data_dir / HISTORY_FILE
    if _line_count(hist) > HISTORY_LIMIT * SLACK:
        rows = [r for r in _read(hist) if isinstance(r.get("t"), int) and r["t"] >= now - KEEP_SECONDS]
        _rewrite(hist, rows)
    chg = data_dir / CHANGES_FILE
    if _line_count(chg) > CHANGES_LIMIT * SLACK:
        _rewrite(chg, _read(chg)[-CHANGES_LIMIT:])


def record(data_dir, inv: dict, state: dict, previous_state: dict, now: int | None = None) -> None:
    data_dir = Path(data_dir)
    t = int(time.time() if now is None else now)
    vit = scan.section(inv, "vitals", {})
    usage = scan.section(inv, "storage", {}).get("usage", [])
    counts = {"ok": 0, "problem": 0, "todo": 0, "off": 0}
    for entry in state.get("cards", {}).values():
        if entry.get("status") in counts:
            counts[entry["status"]] += 1
    point = {"t": t, "temp_c": vit.get("temp_c"), "load1": vit.get("load1"),
             "mem": vit.get("mem_used_percent"),
             "disk": {u["target"]: u["used_percent"] for u in usage
                      if "target" in u and "used_percent" in u},
             "status": counts}
    _append(data_dir / HISTORY_FILE, [point])

    before = (previous_state or {}).get("cards", {})
    changes = []
    for cid, entry in state.get("cards", {}).items():
        old = before.get(cid)
        if old is not None and old.get("status") != entry.get("status"):
            changes.append({"t": t, "card": cid, "from": old.get("status"), "to": entry.get("status")})
    _append(data_dir / CHANGES_FILE, changes)
    _trim(data_dir, t)


def _thin(points: list[dict]) -> list[dict]:
    if len(points) <= MAX_POINTS:
        return points
    step = -(-len(points) // MAX_POINTS)  # ceil
    # Count from the end so the newest point is always kept.
    return points[::-1][::step][::-1]


def view(data_dir, now: int | None = None) -> dict:
    data_dir = Path(data_dir)
    t = int(time.time() if now is None else now)
    rows = sorted((r for r in _read(data_dir / HISTORY_FILE) if isinstance(r.get("t"), int)),
                  key=lambda r: r["t"])
    changes = _read(data_dir / CHANGES_FILE)[-VIEW_CHANGES:]
    return {"day": _thin([r for r in rows if r["t"] >= t - 86400]),
            "week": _thin([r for r in rows if r["t"] >= t - 7 * 86400]),
            "changes": changes[::-1]}
