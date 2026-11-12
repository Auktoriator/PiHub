"""Command line: scan the machine, check the cards, look a card up."""
from __future__ import annotations

import argparse
import getpass
import os
import signal
import sys
import tomllib
from pathlib import Path

from . import __version__
from . import cards as cardlib
from . import auth, scan, state
from .texts import Words

DEFAULT_SOCKET = "/run/pihub/runner.sock"  # same as runner.DEFAULT_SOCKET, without importing the runner here


def data_dir(arg: str | None) -> Path:
    return Path(arg or os.environ.get("PIHUB_DATA") or Path.home() / "pihub-data").expanduser()


def language(data: Path) -> str:
    try:
        lang = tomllib.loads((data / "settings.toml").read_text()).get("language")
        if lang:
            return lang
    except (OSError, tomllib.TOMLDecodeError):
        pass
    return (os.environ.get("LANG") or "en")[:2]


def cmd_scan(data: Path, t: Words) -> int:
    inv = scan.collect()
    target = data / "inventory.json"
    scan.write(inv, target)
    for name, s in inv["sections"].items():
        print(t("scan_section_ok", name=name) if s["ok"] else t("scan_section_fail", name=name, error=s["error"]))
        if not s["ok"] and s.get("why") == scan.NEEDS_SUDO:
            print("  " + t("scan_needs_sudo"))
    print(t("scan_done", path=target))
    return 0


def cmd_collect(data: Path, t: Words) -> int:
    inv, st, problems = state.collect(data)
    for name, s in inv["sections"].items():
        if not s["ok"]:
            print(t("scan_section_fail", name=name, error=s["error"]))
    if not problems and not st["cards"]:
        problems = [("cards", "no_cards", {"path": data / "cards"})]
    if not st["cards"]:
        print(t("no_cards", path=data / "cards"))
        print(t("first_steps", data=data))
    counts = {}
    for entry in st["cards"].values():
        counts[entry["status"]] = counts.get(entry["status"], 0) + 1
    print(t("collect_done", ok=counts.get("ok", 0), problem=counts.get("problem", 0),
            todo=counts.get("todo", 0), off=counts.get("off", 0), path=data / "state.json"))
    return 1 if problems else 0


def cmd_serve(data: Path, lang: str, host: str, port: int, runner_socket: str) -> int:
    from . import web
    try:
        names = tomllib.loads((data / "settings.toml").read_text()).get("names", [])
    except (OSError, tomllib.TOMLDecodeError):
        names = []
    web.serve(data, lang, host, port, names, runner_socket)
    return 0


def cmd_password(data: Path, t: Words) -> int:
    """Ask twice, never take the password as an argument, never print it."""
    if not sys.stdin.isatty():
        print(t("password_need_tty"))
        return 1
    try:
        first = getpass.getpass(t("password_prompt", min=auth.MIN_LENGTH))
        second = getpass.getpass(t("password_again"))
    except (EOFError, KeyboardInterrupt):
        print("\n" + t("password_aborted"))
        return 1
    if first != second:
        print(t("password_mismatch"))
        return 1
    try:
        auth.set_password(data, first)
    except auth.PasswordError as e:
        print(t(e.key, min=auth.MIN_LENGTH))
        return 1
    print(t("password_saved", path=auth.login_file(data)))
    return 0


def cmd_runner(data: Path, t: Words, socket_path: str) -> int:
    from . import runner
    signal.signal(signal.SIGTERM, signal.default_int_handler)  # systemd stop: leave cleanly
    runner.harden()
    try:
        server = runner.make_server(data, socket_path)
    except OSError as e:
        print(t("runner_error", error=e), file=sys.stderr)
        return 1
    print(t("runner_listening", path=socket_path), flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
        try:
            os.unlink(socket_path)
        except OSError:
            pass
    return 0


def cmd_check(data: Path, t: Words) -> int:
    areas, cards, problems = cardlib.load(data)
    if not cards:
        print(t("no_cards", path=data / "cards"))
        print(t("first_steps", data=data))
        return 1
    if problems:
        print(t("problems", n=len(problems)))
        for where, key, params in problems:
            print(f"  {where}: {t(key, **params)}")
    else:
        print(t("cards_ok", cards=len(cards), areas=len(areas)))

    try:
        inv = scan.load(data / "inventory.json")
    except OSError:
        print(t("no_inventory"))
        return 1
    unread = [n for n in ("units", "containers", "ports") if scan.unreadable(inv, n)]
    for name in unread:
        print(t("check_unread", name=name, error=inv["sections"][name].get("error", "")))
    cov = cardlib.coverage((scan.section(inv, "units", {}), scan.section(inv, "containers", []),
                            scan.section(inv, "ports", {}).get("listening", [])), cards, unread)

    print("\n" + t("unclaimed", n=len(cov["unclaimed"])))
    for kind, name, detail in cov["unclaimed"]:
        print(f"  {t(kind):<10} {name:<36} {detail}")
    if not cov["unclaimed"]:
        print(t("nothing"))
    if cov["high_ports"]:
        print(t("high_ports", n=cov["high_ports"]))

    print("\n" + t("missing", n=len(cov["missing"])))
    for kind, name, card in cov["missing"]:
        print(f"  {t(kind):<10} {name:<36} {card}")
    if not cov["missing"]:
        print(t("nothing"))

    print("\n" + t("port_notes", n=len(cov["notes"])))
    for key, params in cov["notes"]:
        if "reach" in params:
            params["reach"] = ", ".join(t("reach_" + r) for r in params["reach"])
        if "bound" in params:
            params["bound"] = t("bound_" + params["bound"])
        print("  " + t(key, **params))
    if not cov["notes"]:
        print(t("nothing"))
    return 1 if problems else 0


def cmd_list(data: Path, t: Words) -> int:
    areas, cards, _ = cardlib.load(data)
    if not cards:
        print(t("no_cards", path=data / "cards"))
        print(t("first_steps", data=data))
        return 1
    for area in areas:
        print(f"\n{area.get('name')}  ({area.get('summary', '')})")
        for cid, card in cards.items():
            if card.get("area") == area.get("id"):
                print(f"  {cid:<14} {card.get('name', ''):<26} {card.get('summary', '')}")
    return 0


def cmd_show(data: Path, t: Words, cid: str) -> int:
    areas, cards, _ = cardlib.load(data)
    card = cards.get(cid)
    if card is None:
        if not cards:
            print(t("no_cards", path=data / "cards"))
            print(t("first_steps", data=data))
        else:
            print(t("unknown_card", id=cid, known=", ".join(sorted(cards))))
        return 1
    area = next((a.get("name") for a in areas if a.get("id") == card.get("area")), card.get("area"))

    def head(key):
        print(f"\n{t(key).upper()}")

    print(f"{card.get('name')}  ·  {t('h_area')}: {area}")
    print(card.get("summary", ""))
    head("h_about")
    print(card.get("about", "").strip())
    if card.get("switch"):
        print(f"\n{t('h_switch')}: {card['switch']}")
    if card.get("ports"):
        head("h_ports")
        for p in card["ports"]:
            reach = ", ".join(t("reach_" + r) for r in p["reach"] if r in cardlib.REACH)
            print(f"  {p.get('port')}/{p.get('proto', 'tcp')}  {reach}: {p.get('why')}")
    if card.get("links"):
        head("h_links")
        for link in card["links"]:
            where = link.get("url") or (f"Port {link['port']}{link.get('path', '')}" if link.get("port") else "")
            print(f"  {link.get('label')}  {where}  {link.get('note', '')}".rstrip())
    if card.get("checks"):
        head("h_checks")
        for c in card["checks"]:
            print(f"  {c.get('label')}\n      $ {c.get('run')}")
            if c.get("blind_spot"):
                print(f"      {t('blind_spot')}: {c['blind_spot']}")
    if card.get("steps"):
        head("h_steps")
        for n, s in enumerate(card["steps"], 1):
            print(f"  {n}. {s.get('text')}")
            if s.get("run"):
                print(f"      $ {s['run']}")
    if card.get("actions"):
        head("h_actions")
        for a in card["actions"]:
            print(f"  {a.get('label')}  [{t('level_' + a.get('level', 'copy'))}]\n      $ {a.get('run')}")
            if a.get("effect"):
                print(f"      {a['effect']}")
    if card.get("needs"):
        head("h_needs")
        print("  " + ", ".join(cards[n].get("name", n) for n in card["needs"] if n in cards))
    used_by = [c.get("name", i) for i, c in cards.items() if cid in c.get("needs", [])]
    if used_by:
        head("h_used_by")
        print("  " + ", ".join(used_by))
    if card.get("places"):
        head("h_places")
        for p in card["places"]:
            print(f"  {p.get('path')}  {p.get('what')}")
            if p.get("caution"):
                print(f"      {t('caution')}: {p['caution']}")
    return 0


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="pihub", description="PiHub: know your Raspberry Pi.")
    parser.add_argument("--version", action="version", version=f"pihub {__version__}")
    parser.add_argument("--data", help="folder with this machine's cards and inventory (default: ~/pihub-data)")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("scan", help="read the machine and write the inventory (changes nothing)")
    sub.add_parser("check", help="check the cards and compare them with the inventory")
    sub.add_parser("collect", help="scan, run the checks on the cards, write the current state")
    web = sub.add_parser("serve", help="show the web page (runs nothing itself; passes actions on to the runner)")
    web.add_argument("--host", default="127.0.0.1", help="address to listen on (default: this machine only)")
    web.add_argument("--port", type=int, default=8096)
    web.add_argument("--runner-socket", default=DEFAULT_SOCKET, help="socket of the runner (default: %(default)s)")
    sub.add_parser("password", help="set the password for running actions (asked twice, in a terminal)")
    run = sub.add_parser("runner", help="the one part that runs actions; the web page talks to it")
    run.add_argument("--socket", default=DEFAULT_SOCKET, help="where to listen (default: %(default)s)")
    sub.add_parser("list", help="list all cards by area")
    show = sub.add_parser("show", help="show one card")
    show.add_argument("id")
    args = parser.parse_args(argv)

    data = data_dir(args.data)
    t = Words(language(data))
    if args.command == "scan":
        return cmd_scan(data, t)
    if args.command == "collect":
        return cmd_collect(data, t)
    if args.command == "serve":
        return cmd_serve(data, t.lang, args.host, args.port, args.runner_socket)
    if args.command == "password":
        return cmd_password(data, t)
    if args.command == "runner":
        return cmd_runner(data, t, args.socket)
    if args.command == "check":
        return cmd_check(data, t)
    if args.command == "list":
        return cmd_list(data, t)
    return cmd_show(data, t, args.id)
