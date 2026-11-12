"""What the pages Security, Storage and Files show, computed from the
inventory and the cards. Kept apart from the web server so it can be tested.
"""
from __future__ import annotations

import ipaddress
import re

from . import scan
from .cards import HIGH_PORT

RULE_PORT = re.compile(r"^(\d+)(?::(\d+))?(?:/(tcp|udp))?\b")  # "22", "22/tcp", "6000:6010/tcp"
SHARED_SPACE = ipaddress.ip_network("100.64.0.0/10")  # carrier-grade NAT; Tailscale hands out addresses here
WIDTH = {"local": 0, "address": 1, "all": 2}


def _source_kind(source: str) -> str:
    text = source.replace("(v6)", "").strip()
    if text == "Anywhere":
        return "internet"
    try:
        net = ipaddress.ip_network(text, strict=False)
    except ValueError:
        return "other"
    if net.version == 4 and net.subnet_of(SHARED_SPACE):
        return "tailscale"
    return "lan" if (net.is_private or net.is_link_local) else "other"


def firewall_for(port: int, proto: str, rules: list) -> tuple[list, list]:
    """Which groups the firewall lets in to this port, and the rules that say so.

    A simplification on purpose: it reads each rule on its own and does not
    replay the order of the rule set. A rule that denies everyone wins.
    """
    lets_in, used, denied = set(), [], False
    for rule in rules:
        m = RULE_PORT.match(rule["to"])
        if not m or (m.group(3) and m.group(3) != proto):
            continue
        low, high = int(m.group(1)), int(m.group(2) or m.group(1))
        if not low <= port <= high:
            continue
        used.append(f"{rule['to']}  {rule['action']}  {rule['from']}")
        iface = re.search(r"\bon (\S+)", rule["to"])
        if rule["action"].startswith("ALLOW") or rule["action"].startswith("LIMIT"):
            if iface and iface.group(1).startswith("tailscale"):
                lets_in.add("tailscale")
            else:
                lets_in.add(_source_kind(rule["from"]))
        elif _source_kind(rule["from"]) == "internet" and not iface:
            denied = True
    if denied:
        lets_in.discard("internet")
    order = ["internet", "lan", "tailscale", "other"]
    return [k for k in order if k in lets_in], used


def security(inv: dict, cards: dict) -> dict:
    listening = scan.section(inv, "ports", {}).get("listening", [])
    fw = scan.section(inv, "firewall", {"status": "", "default": "", "rules": []})
    blocks = scan.section(inv, "blocks", {"decisions": []})
    # "unreadable": no firewall tool, or no right to ask it. Then nothing can be said
    # about who gets in, and the page must not pretend the ports are shut.
    # "inactive": ufw is there but switched off, so nothing is shut either.
    if scan.unreadable(inv, "firewall"):
        fw_state = "unreadable"
    elif fw["status"].lower().startswith("inactive"):
        fw_state = "inactive"
    else:
        fw_state = "active"

    intended = {}
    for cid, card in cards.items():
        for p in card.get("ports", []):
            if isinstance(p.get("port"), int):
                intended[(p["port"], p.get("proto", "tcp"))] = (cid, p)

    merged: dict[tuple, dict] = {}
    for row in listening:
        key = (row["port"], row["proto"])
        e = merged.setdefault(key, {"bound": "local", "processes": set()})
        if WIDTH[row["bound"]] > WIDTH[e["bound"]]:
            e["bound"] = row["bound"]
        e["processes"].update(row["processes"])

    rows = []
    for (port, proto) in sorted(set(merged) | set(intended)):
        if (port, proto) not in intended and port >= HIGH_PORT:
            continue
        m = merged.get((port, proto))
        cid, p = intended.get((port, proto), (None, None))
        lets_in, used = firewall_for(port, proto, fw["rules"])
        if fw_state == "inactive":
            lets_in, used = ["internet", "lan"], []
        elif fw_state == "unreadable":
            lets_in, used = [], []
        effective = [] if (m is None or m["bound"] == "local") else lets_in
        meant = [r for r in (p["reach"] if p else []) if r != "local"]
        if p is None:
            verdict = "no_card"
        elif m is None:
            verdict = "not_listening"
        elif fw_state == "unreadable" and m["bound"] != "local":
            verdict = "unverified"
        elif set(meant) == set(effective):
            verdict = "as_meant"
        else:
            verdict = "differs"
        processes = sorted(m["processes"]) if m else []
        rows.append({
            "port": port, "proto": proto, "processes": processes,
            "bound": m["bound"] if m else None,
            "card": cid, "meant": p["reach"] if p else [], "why": p["why"] if p else "",
            "firewall": lets_in, "rules": used, "effective": effective, "verdict": verdict,
            # Shut only because a firewall rule (or the default) says so.
            "firewall_only": bool(m and m["bound"] != "local" and not effective and fw_state == "active"),
            # Ports published by Docker are forwarded before ufw sees them.
            "docker": bool(m and m["bound"] != "local" and "docker-proxy" in processes),
        })

    bans = [{"address": r["from"], "comment": r["comment"]} for r in fw["rules"]
            if r["to"].startswith("Anywhere") and r["action"].startswith("DENY")
            and not r["from"].startswith("Anywhere")]
    return {"firewall": {"tool": fw.get("tool", ""), "status": fw["status"], "default": fw["default"],
                         "state": fw_state},
            "ports": rows, "blocks": blocks["decisions"], "bans": bans}


def files(inv: dict, cards: dict) -> dict:
    home = scan.section(inv, "home", {"path": "", "entries": []})
    places = []
    for cid, card in cards.items():
        for p in card.get("places", []):
            places.append({"path": p["path"], "what": p["what"], "caution": p.get("caution", ""),
                           "card": cid})
    entries = []
    for e in home["entries"]:
        prefix = "~/" + e["name"]
        known = [p for p in places if p["path"] == prefix or p["path"].startswith(prefix + "/")]
        entries.append({**e, "places": known})
    elsewhere = [p for p in places if not p["path"].startswith("~/")]
    return {"home": home["path"], "entries": entries, "elsewhere": elsewhere}


def apply_mutes(current: dict, muted: dict) -> dict:
    """The state with silenced findings marked, and every card's status worked
    out again from the findings that still count.

    A finding whose key is in `muted` gets `muted: true`, `reason` and `since`.
    A card is `problem` if it has a finding that is not silenced and not a
    `todo`; else `todo` if it has a `todo` that is not silenced; else `ok`.
    A card that is `off` stays `off`. The input is not changed.
    """
    muted = muted if isinstance(muted, dict) else {}
    findings = []
    for f in current.get("findings", []):
        entry = muted.get(f.get("key")) if f.get("key") else None
        if isinstance(entry, dict):
            f = {**f, "muted": True, "reason": str(entry.get("reason", "")), "since": str(entry.get("since", ""))}
        findings.append(f)
    cards = {}
    for cid, entry in current.get("cards", {}).items():
        if entry.get("status") == "off":
            cards[cid] = entry
            continue
        live = [f for f in findings if f.get("card") == cid and not f.get("muted")]
        if any(f.get("kind") != "todo" for f in live):
            status = "problem"
        elif live:
            status = "todo"
        else:
            status = "ok"
        cards[cid] = {**entry, "status": status}
    return {**current, "cards": cards, "findings": findings}
