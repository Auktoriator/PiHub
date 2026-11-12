"""Cards: what is known about each part of this machine, as data.

A card is one TOML file in <data>/cards/. It says in plain words what a
service is, what it needs, which ports it means to open and for whom, how
to tell that it works, what to do when it does not, and which commands are
allowed. The scan says what is there; the cards say what it means. Where
the two disagree, that disagreement is the finding.

Problems are returned as (where, key, params) so they can be shown in the
owner's language.
"""
from __future__ import annotations

import tomllib
from pathlib import Path

SCHEMA = 1
LEVELS = ("read", "change", "copy")
REACH = ("internet", "lan", "tailscale", "local")
SUMMARY_MAX = 70
TOP_REQUIRED = ("id", "name", "area", "summary", "about")
TOP_LISTS = ("units", "containers", "needs")
TABLES = {  # table: (required keys, optional keys)
    "ports": (("port", "reach", "why"), ("proto",)),
    "links": (("label",), ("port", "scheme", "url", "path", "note")),
    "places": (("path", "what"), ("caution",)),
    "checks": (("label", "run"), ("expect", "blind_spot", "show")),
    "steps": (("text",), ("run",)),
    "actions": (("label", "run", "level"), ("effect", "timeout", "choose")),
}
TOP_KEYS = {"schema", "switch", "todo", *TOP_REQUIRED, *TOP_LISTS, *TABLES}
HIGH_PORT = 32768
TIMEOUT_DEFAULT, TIMEOUT_MAX = 120, 3600
CHOICE_SOURCES = ("blocked_addresses", "unmounted_targets")  # must match actions.CHOICES


def load(data_dir: Path):
    """Return (areas, cards, problems)."""
    problems = []
    areas = []
    try:
        areas = tomllib.loads((data_dir / "areas.toml").read_text()).get("areas", [])
    except (OSError, tomllib.TOMLDecodeError):
        pass
    if not areas:
        problems.append(("areas.toml", "p_no_areas", {}))
    area_ids = {a.get("id") for a in areas}

    cards = {}
    for path in sorted((data_dir / "cards").glob("*.toml")):
        where = path.name
        try:
            card = tomllib.loads(path.read_text())
        except (OSError, tomllib.TOMLDecodeError) as e:
            problems.append((where, "p_parse", {"error": str(e)}))
            continue
        for key in TOP_REQUIRED:
            if not card.get(key):
                problems.append((where, "p_required", {"key": key}))
        for key in card.keys() - TOP_KEYS:
            problems.append((where, "p_unknown", {"key": key}))
        if card.get("id") != path.stem:
            problems.append((where, "p_id", {"id": card.get("id")}))
        if card.get("area") not in area_ids and areas:
            problems.append((where, "p_area", {"area": card.get("area")}))
        if len(card.get("summary", "")) > SUMMARY_MAX:
            problems.append((where, "p_long", {"max": SUMMARY_MAX}))
        for table, (required, optional) in TABLES.items():
            for n, entry in enumerate(card.get(table, []), 1):
                for key in required:
                    if key not in entry:
                        problems.append((where, "p_entry_required", {"table": table, "n": n, "key": key}))
                for key in entry.keys() - set(required) - set(optional):
                    problems.append((where, "p_entry_unknown", {"table": table, "n": n, "key": key}))
        for n, port in enumerate(card.get("ports", []), 1):
            if not isinstance(port.get("port"), int):
                problems.append((where, "p_port", {"n": n}))
            reach = port.get("reach", [])
            port["reach"] = [reach] if isinstance(reach, str) else list(reach)
            for value in port["reach"]:
                if value not in REACH:
                    problems.append((where, "p_reach", {"n": n, "value": value, "allowed": ", ".join(REACH)}))
        for n, action in enumerate(card.get("actions", []), 1):
            level = action.get("level")
            if level not in LEVELS:
                problems.append((where, "p_level", {"n": n, "value": level, "allowed": ", ".join(LEVELS)}))
            elif level != "read" and not action.get("effect"):
                problems.append((where, "p_effect", {"n": n, "level": level}))
            if "timeout" in action:
                t = action["timeout"]
                if not isinstance(t, int) or isinstance(t, bool) or not 1 <= t <= TIMEOUT_MAX:
                    problems.append((where, "p_timeout", {"n": n, "value": t, "max": TIMEOUT_MAX}))
            if "choose" in action and action["choose"] not in CHOICE_SOURCES:
                problems.append((where, "p_choose", {"n": n, "value": action["choose"],
                                                     "allowed": ", ".join(CHOICE_SOURCES)}))
            if isinstance(action.get("run"), str):
                places = action["run"].count("{value}")
                if places != (1 if "choose" in action else 0):
                    problems.append((where, "p_value", {"n": n}))
        cards[path.stem] = card

    for cid, card in cards.items():
        for need in card.get("needs", []):
            if need not in cards:
                problems.append((cid + ".toml", "p_need", {"need": need}))
    return areas, cards, problems


def stem(unit: str) -> str:
    """minecraft.service and minecraft.timer are one thing on a card."""
    return unit.rsplit(".", 1)[0]


def coverage(inv_sections, cards: dict, unread=()) -> dict:
    """Compare what the scan found with what the cards describe.

    `unread` names the sections ("units", "containers", "ports") the scan could
    not read. Nothing is claimed about those: a card's unit is not "missing"
    just because systemctl could not be asked."""
    units, containers, listening = inv_sections

    claimed_units = set()
    for card in cards.values():
        claimed_units.update(stem(u) for u in card.get("units", []))
        if card.get("switch"):
            claimed_units.add(stem(card["switch"]))
    claimed_containers = {c for card in cards.values() for c in card.get("containers", [])}
    claimed_ports = {p["port"]: cid for cid, card in cards.items()
                     for p in card.get("ports", []) if isinstance(p.get("port"), int)}

    by_port: dict[int, dict] = {}
    for row in listening:
        entry = by_port.setdefault(row["port"], {"bound": set(), "processes": set(), "protos": set()})
        entry["bound"].add(row["bound"])
        entry["processes"].update(row["processes"])
        entry["protos"].add(row["proto"])

    unclaimed = []
    for name in sorted(units):
        u = units[name]
        switched_off = u.get("enabled") in ("disabled", "masked") and u["active"] not in ("active", "failed")
        if u["own"] and stem(name) not in claimed_units and not switched_off:
            unclaimed.append(("unit", name, u["active"]))
    for c in containers:
        if c["name"] not in claimed_containers:
            unclaimed.append(("container", c["name"], c["state"]))
    high = 0
    for port in sorted(by_port):
        if port in claimed_ports:
            continue
        if port >= HIGH_PORT:
            high += 1
            continue
        e = by_port[port]
        unclaimed.append(("port", f"{port}/{'+'.join(sorted(e['protos']))}",
                          ", ".join(sorted(e["processes"])) or "?"))

    missing = []
    present = {c["name"] for c in containers}
    for cid, card in sorted(cards.items()):
        for u in card.get("units", []) + ([card["switch"]] if card.get("switch") else []):
            if u not in units and "units" not in unread:
                missing.append(("unit", u, cid))
        for c in card.get("containers", []):
            if c not in present and "containers" not in unread:
                missing.append(("container", c, cid))

    notes = []  # (key, params): intended reach against measured binding
    for cid, card in sorted(cards.items()):
        for p in card.get("ports", []):
            port = p.get("port")
            if not isinstance(port, int) or "ports" in unread:
                continue
            label = f"{port}/{p.get('proto', 'tcp')}"
            bound = by_port.get(port, {}).get("bound", set())
            if not bound:
                notes.append(("n_not_listening", {"port": label, "card": cid}))
            elif p["reach"] != ["local"] and bound == {"local"}:
                notes.append(("n_only_local", {"port": label, "card": cid, "reach": p["reach"]}))
            elif p["reach"] == ["local"] and bound != {"local"}:
                widest = "all" if "all" in bound else "address"
                notes.append(("n_wider", {"port": label, "card": cid, "bound": widest}))
    return {"unclaimed": unclaimed, "high_ports": high, "missing": missing, "notes": notes}
