"""Current state: the inventory plus the result of every card's checks.

`collect` is the only part of PiHub that runs commands, and it runs only
what the cards list under `checks`. The web page never runs anything; it
shows what `collect` wrote.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path

from . import cards as cardlib
from . import history, scan

CHECK_TIMEOUT = 20
DETAIL_MAX = 300
SHOW_MAX = 120


def run_check(check: dict) -> dict:
    try:
        p = subprocess.run(["bash", "-c", check["run"]], capture_output=True, text=True,
                           timeout=CHECK_TIMEOUT, stdin=subprocess.DEVNULL)
    except subprocess.TimeoutExpired:
        return {"label": check["label"], "ok": False, "timeout": True, "detail": ""}
    out = (p.stdout + p.stderr).strip()
    ok = p.returncode == 0 and (not check.get("expect") or re.search(check["expect"], out) is not None)
    # Output is kept for a failed check: it is the clue. A passing check
    # keeps nothing, unless the card asks to show its first line (a reading
    # such as "last backup 3 h ago").
    if not ok:
        detail = out[-DETAIL_MAX:]
    elif check.get("show") and out:
        detail = out.splitlines()[0][:SHOW_MAX]
    else:
        detail = ""
    return {"label": check["label"], "ok": ok, "timeout": False, "detail": detail}


def unit_state(unit: dict | None) -> str:
    if unit is None:
        return "missing"
    if unit["active"] == "failed":
        return "failed"
    if unit["active"] in ("active", "activating", "reloading"):
        return "running"
    if unit["enabled"] in ("enabled", "enabled-runtime"):
        return "stopped"  # switched on, but not running
    return "idle"  # not meant to run all the time (started by a timer, or switched off)


def build(inv: dict, cards: dict, check_runner=run_check) -> dict:
    units = scan.section(inv, "units", {})
    containers = {c["name"]: c for c in scan.section(inv, "containers", [])}
    listening = scan.section(inv, "ports", {}).get("listening", [])
    # What could not be read says nothing about the cards: no "missing" unit,
    # no "switched off", no "nothing listens" because the tool was absent.
    unread = {name for name in ("units", "containers", "ports") if scan.unreadable(inv, name)}
    cov = cardlib.coverage((units, list(containers.values()), listening), cards, unread)
    port_notes: dict[str, list] = {}
    for key, params in cov["notes"]:
        port_notes.setdefault(params["card"], []).append((key, params))

    out_cards, findings = {}, []
    for cid, card in cards.items():
        entry = {"status": "ok", "units": {}, "containers": {}, "checks": []}
        switch = card.get("switch")
        if switch and "units" not in unread and units.get(switch, {}).get("active") != "active":
            entry["status"] = "off"
            out_cards[cid] = entry
            continue

        problems = []
        for name in card.get("units", []):
            state = "unknown" if "units" in unread else unit_state(units.get(name))
            entry["units"][name] = state
            if state in ("failed", "stopped", "missing"):
                problems.append({"kind": "unit_" + state, "name": name})
        for name in card.get("containers", []):
            state = ("unknown" if "containers" in unread
                     else containers[name]["state"] if name in containers else "missing")
            entry["containers"][name] = state
            if state not in ("running", "unknown"):
                problems.append({"kind": "container_missing" if state == "missing" else "container_down",
                                 "name": name})
        for check in card.get("checks", []):
            result = check_runner(check)
            entry["checks"].append(result)
            if not result["ok"]:
                problems.append({"kind": "check_failed", "label": check["label"], "run": check["run"],
                                 "detail": result["detail"], "timeout": result["timeout"]})
        for key, params in port_notes.get(cid, []):
            if key in ("n_only_local", "n_not_listening"):
                problems.append({"kind": "port_" + key[2:], "port": params["port"],
                                 "reach": params.get("reach", [])})

        if problems:
            entry["status"] = "problem"
        elif card.get("todo"):
            entry["status"] = "todo"
        for p in problems:
            part = p.get("name") or p.get("label") or p.get("port") or ""
            findings.append({"card": cid, "key": f"{cid}:{p['kind']}:{part}", **p})
        if card.get("todo"):
            findings.append({"card": cid, "key": f"{cid}:todo:", "kind": "todo", "text": card["todo"]})
        out_cards[cid] = entry

    if cov["unclaimed"]:
        findings.append({"card": None, "key": "undescribed", "kind": "undescribed",
                         "items": [{"kind": k, "name": n, "detail": d} for k, n, d in cov["unclaimed"]]})
    return {"taken_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"), "cards": out_cards, "findings": findings}


def collect(data: Path) -> tuple[dict, dict, list]:
    inv = scan.collect()
    scan.write(inv, data / "inventory.json")
    _, cards, problems = cardlib.load(data)
    previous = load(data)
    state = build(inv, cards)
    scan.write(state, data / "state.json")
    try:
        history.record(data, inv, state, previous)
    except Exception as e:  # history must never fail a collection run
        print(f"pihub: history not recorded: {type(e).__name__}: {e}", file=sys.stderr)
    return inv, state, problems


def load(data: Path) -> dict:
    try:
        return json.loads((data / "state.json").read_text())
    except (OSError, ValueError):
        return {"taken_at": "", "cards": {}, "findings": []}
