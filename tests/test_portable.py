"""Run with: python3 -m unittest tests.test_portable

PiHub grew on one machine. These tests keep it honest on any other: a machine
without ufw, CrowdSec, Docker, nginx or lsblk, without passwordless sudo, or a
data folder that is still empty. Nothing may crash, nothing may claim what was
not read, and the page must still draw in full with a sentence at the gap.
Only invented data; nothing here reads or runs anything of a real card.
"""
import contextlib
import io
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from pihub import __version__, cards as cardlib, cli, scan, state, views, web  # noqa: E402
from pihub.texts import PAGE, TEXTS  # noqa: E402

sys.path.insert(0, str(ROOT / "tests"))
from test_page import AREAS  # noqa: E402

CARDS = {
    "site": '''id = "site"
name = "Site"
area = "web"
summary = "An invented site"
about = "Only for tests."
units = ["web.service"]
containers = ["webapp"]
switch = "holiday.target"

[[ports]]
port = 80
reach = ["lan"]
why = "The site."

[[ports]]
port = 9999
reach = ["internet"]
why = "Never listens."

[[checks]]
label = "Answers"
run = "true"
''',
    "disks": '''id = "disks"
name = "Disks"
area = "sys"
summary = "An invented disk card"
about = "Only for tests."

[[actions]]
label = "Mount a target"
run = "echo mount {value}"
level = "change"
choose = "unmounted_targets"
effect = "The disk appears."
''',
}


def bad(why, error, tool=""):
    return {"ok": False, "error": error, "why": why, "tool": tool}


def good(data):
    return {"ok": True, "data": data}


def degraded_inventory():
    """firewall, blocks, containers, web and storage could not be read."""
    return {"taken_at": "2026-11-12T10:00:00+0100", "sections": {
        "host": good({"name": "box", "model": "", "os": "Some Linux"}),
        "units": good({"web.service": {"active": "active", "sub": "running", "enabled": "enabled", "own": True},
                       "holiday.target": {"active": "active", "sub": "active", "enabled": "enabled", "own": True}}),
        "timers": good([]),
        "containers": bad("needs_sudo", "permission denied while trying to connect to the Docker daemon", "docker"),
        "ports": good({"process_names": False, "listening": [
            {"proto": "tcp", "port": 80, "address": "0.0.0.0", "bound": "all", "processes": []},
            {"proto": "tcp", "port": 631, "address": "127.0.0.1", "bound": "local", "processes": []}]}),
        "firewall": bad("missing_tool", "ufw: not installed", "ufw"),
        "blocks": bad("missing_tool", "cscli: not installed", "cscli"),
        "storage": bad("failed", "lsblk: unknown column", "lsblk"),
        "web": bad("missing_tool", "nginx: no sites-enabled folder", "nginx"),
        "home": good({"path": "/home/someone", "entries": [{"name": "notes", "kind": "dir", "git": False}]}),
        "vitals": good({"temp_c": None, "load1": 0.2, "mem_used_percent": 30, "uptime_s": 100}),
    }}


def write_data(root: Path, inv: dict | None) -> Path:
    data = root / "data"
    (data / "cards").mkdir(parents=True)
    (data / "areas.toml").write_text(AREAS)
    for cid, text in CARDS.items():
        (data / "cards" / f"{cid}.toml").write_text(text)
    if inv is not None:
        (data / "inventory.json").write_text(json.dumps(inv))
        _, loaded, problems = cardlib.load(data)
        assert not problems, problems
        (data / "state.json").write_text(json.dumps(state.build(inv, loaded)))
    return data


def run_cli(*args, lang="en", home=None):
    out = io.StringIO()
    env = {"LANG": lang, "HOME": str(home or tempfile.gettempdir())}
    with mock.patch.dict(os.environ, env), contextlib.redirect_stdout(out):
        code = cli.main(list(args))
    return code, out.getvalue()


class Tmp(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp)


class Unreadable(Tmp):
    def test_state_does_not_blame_cards_for_what_was_not_read(self):
        inv = degraded_inventory()
        _, loaded, _ = cardlib.load(write_data(self.tmp, None))
        result = state.build(inv, loaded, lambda c: {"label": c["label"], "ok": True, "timeout": False, "detail": ""})
        self.assertEqual(result["cards"]["site"]["containers"], {"webapp": "unknown"})
        self.assertEqual([f for f in result["findings"] if f["kind"].startswith("container")], [])
        # 9999 does not listen, and ports were read: that is still a finding.
        self.assertIn("port_not_listening", [f["kind"] for f in result["findings"]])

    def test_unreadable_units_and_ports_claim_nothing(self):
        inv = degraded_inventory()
        inv["sections"]["units"] = bad("failed", "System has not been booted with systemd", "systemctl")
        inv["sections"]["ports"] = bad("missing_tool", "ss: not installed", "ss")
        _, loaded, _ = cardlib.load(write_data(self.tmp, None))
        result = state.build(inv, loaded, lambda c: {"label": c["label"], "ok": True, "timeout": False, "detail": ""})
        self.assertEqual(result["cards"]["site"]["status"], "ok")  # not "off" because the switch could not be read
        self.assertEqual(result["cards"]["site"]["units"], {"web.service": "unknown"})
        self.assertEqual(result["findings"], [])

    def test_security_says_it_cannot_verify(self):
        _, loaded, _ = cardlib.load(write_data(self.tmp, None))
        sec = views.security(degraded_inventory(), loaded)
        self.assertEqual(sec["firewall"]["state"], "unreadable")
        row = next(r for r in sec["ports"] if r["port"] == 80)
        self.assertEqual(row["verdict"], "unverified")
        self.assertEqual((row["firewall"], row["effective"], row["firewall_only"]), ([], [], False))
        self.assertEqual(sec["blocks"], [])
        self.assertEqual(views.files(degraded_inventory(), loaded)["home"], "/home/someone")

    def test_inactive_firewall_shuts_nothing(self):
        inv = degraded_inventory()
        inv["sections"]["firewall"] = good({"tool": "ufw", "status": "inactive", "default": "", "rules": []})
        _, loaded, _ = cardlib.load(write_data(self.tmp, None))
        sec = views.security(inv, loaded)
        row = next(r for r in sec["ports"] if r["port"] == 80)
        self.assertEqual(sec["firewall"]["state"], "inactive")
        self.assertIn("internet", row["effective"])
        self.assertFalse(row["firewall_only"])

    def test_firewall_rules_by_range_and_tailscale_addresses(self):
        rules = [{"to": "6000:6010/tcp", "action": "ALLOW IN", "from": "Anywhere", "comment": ""},
                 {"to": "22", "action": "ALLOW IN", "from": "100.101.102.103", "comment": ""},
                 {"to": "22 (v6)", "action": "ALLOW IN", "from": "fe80::/10", "comment": ""}]
        self.assertEqual(views.firewall_for(6005, "tcp", rules)[0], ["internet"])
        self.assertEqual(views.firewall_for(6011, "tcp", rules)[0], [])
        self.assertEqual(views.firewall_for(22, "tcp", rules)[0], ["lan", "tailscale"])

    def test_payload_and_page_draw_without_the_tools(self):
        data = write_data(self.tmp, degraded_inventory())
        for lang in ("en", "de"):
            p = web.payload(data, lang)
            self.assertFalse(p["sections"]["firewall"]["ok"])
            self.assertEqual(p["sections"]["blocks"]["tool"], "cscli")
            self.assertFalse(p["process_names"])
            self.assertTrue(p["inventory"])
            T = p["texts"]
            wants = [
                T["sec_no_firewall"], T["sec_no_crowdsec"], T["sec_no_names"], T["u_unknown"],
                T["sx_sudo"].format(what=T["what_containers"], tool="docker",
                                    error="permission denied while trying to connect to the Docker daemon"),
                T["sx_failed"].format(what=T["what_storage"], error="lsblk: unknown column"),
            ]
            self.draw(p, wants)

    def draw(self, payload, wants):
        node = shutil.which("node")
        if not node:
            self.skipTest("node is not installed")
        path = self.tmp / f"payload-{payload['lang']}.json"
        path.write_text(json.dumps(payload))
        run = subprocess.run([node, str(ROOT / "tests" / "page_smoke.mjs"), "--draw", str(path), *wants],
                             capture_output=True, text=True, timeout=120)
        self.assertEqual(run.returncode, 0, run.stdout + run.stderr)
        self.assertIn("smoke test: ok", run.stdout)

    def test_a_real_scan_on_a_machine_without_any_of_the_tools(self):
        empty_bin, home = self.tmp / "bin", self.tmp / "home"
        empty_bin.mkdir()
        home.mkdir()
        with mock.patch.object(scan, "SEARCH_PATH", str(empty_bin)), mock.patch.dict(os.environ, {"HOME": str(home)}):
            inv = scan.collect()
        for name in ("units", "timers", "containers", "ports", "firewall", "blocks", "storage"):
            s = inv["sections"][name]
            self.assertFalse(s["ok"], name)
            self.assertEqual(s["why"], "missing_tool", name)
            self.assertTrue(s["tool"], name)
        self.assertTrue(all(inv["sections"][n]["ok"] for n in ("host", "home", "vitals")))
        data = write_data(self.tmp, inv)
        _, loaded, _ = cardlib.load(data)
        current = state.build(inv, loaded)
        self.assertEqual([f["kind"] for f in current["findings"]], [])
        views.security(inv, loaded)
        views.files(inv, loaded)
        p = web.payload(data, "en")
        self.draw(p, [p["texts"]["sec_no_firewall"], p["texts"]["sec_no_crowdsec"]])

    @unittest.skipIf(os.geteuid() == 0, "root does not use sudo")
    def test_sudo_that_asks_for_a_password_is_named_as_such(self):
        bindir = self.tmp / "bin"
        bindir.mkdir()
        for name in ("ufw", "sudo"):
            (bindir / name).write_text("#!/bin/sh\n")
            (bindir / name).chmod(0o755)
        refused = subprocess.CompletedProcess([], 1, "", "sudo: a password is required\n")
        with mock.patch.object(scan, "SEARCH_PATH", str(bindir)), mock.patch("subprocess.run", return_value=refused):
            with self.assertRaises(scan.Unavailable) as caught:
                scan.firewall()
        self.assertEqual((caught.exception.why, caught.exception.tool), ("needs_sudo", "ufw"))
        (bindir / "sudo").unlink()
        with mock.patch.object(scan, "SEARCH_PATH", str(bindir)):
            with self.assertRaises(scan.Unavailable) as caught:
                scan.firewall()
        self.assertEqual(caught.exception.why, "needs_sudo")

    def test_check_names_what_it_could_not_read(self):
        data = write_data(self.tmp, degraded_inventory())
        code, out = run_cli("--data", str(data), "check")
        self.assertIn("containers", out)
        self.assertNotRegex(out, r"webapp\s+site")  # no 'missing container' claim


class EmptyDataFolder(Tmp):
    def test_cli_answers_with_the_first_step(self):
        empty = self.tmp / "nothing-here"
        for lang in ("en", "de"):
            for command in (["check"], ["list"], ["show", "x"]):
                code, out = run_cli("--data", str(empty), *command, lang=lang, home=self.tmp)
                self.assertEqual(code, 1, command)
                self.assertIn(TEXTS[lang]["no_cards"].format(path=empty / "cards"), out)
                self.assertIn(TEXTS[lang]["first_steps"].format(data=empty), out)
        self.assertFalse(empty.exists())  # asking changes nothing

    def test_collect_in_an_empty_folder_runs_and_says_what_to_do(self):
        empty = self.tmp / "data"
        empty.mkdir()
        bindir = self.tmp / "bin"
        bindir.mkdir()
        with mock.patch.object(scan, "SEARCH_PATH", str(bindir)):
            code, out = run_cli("--data", str(empty), "collect", home=self.tmp)
        self.assertEqual(code, 1)
        self.assertIn(TEXTS["en"]["first_steps"].format(data=empty), out)
        self.assertIn("0 fine", out)
        self.assertTrue((empty / "inventory.json").exists())

    def test_scan_prints_the_sudo_hint(self):
        inv = {"sections": {"firewall": bad("needs_sudo", "sudo: a password is required", "ufw"),
                            "host": good({})}}
        with mock.patch.object(scan, "collect", return_value=inv):
            code, out = run_cli("--data", str(self.tmp / "d"), "scan")
        self.assertEqual(code, 0)
        self.assertIn(TEXTS["en"]["scan_needs_sudo"], out)

    def test_page_with_no_cards_and_no_inventory(self):
        data = self.tmp / "data"
        data.mkdir()
        for lang in ("en", "de"):
            p = web.payload(data, lang)
            self.assertEqual((p["cards"], p["areas"], p["inventory"], p["sections"]), ({}, [], False, {}))
            self.assertEqual(p["security"]["ports"], [])
            T = p["texts"]
            self.draw(p, [T["no_inventory_page"], T["empty_cards"]])
        data2 = self.tmp / "never-created"
        web.payload(data2, "en")  # a folder that does not exist yet is the same

    draw = Unreadable.draw


class Examples(unittest.TestCase):
    def test_examples_together_use_every_key_of_the_format(self):
        areas, loaded, problems = cardlib.load(ROOT / "examples")
        self.assertEqual(problems, [])
        self.assertEqual(len(areas), 2)
        self.assertTrue(4 <= len(loaded) <= 5)
        used_top, used_entry = set(), {table: set() for table in cardlib.TABLES}
        for card in loaded.values():
            used_top |= card.keys()
            for table in cardlib.TABLES:
                for entry in card.get(table, []):
                    used_entry[table] |= entry.keys()
        self.assertEqual(sorted(cardlib.TOP_KEYS - used_top), [])
        for table, (required, optional) in cardlib.TABLES.items():
            self.assertEqual(sorted((set(required) | set(optional)) - used_entry[table]), [], table)
        everything = [e for c in loaded.values() for t in cardlib.TABLES for e in c.get(t, [])]
        self.assertEqual({r for c in loaded.values() for p in c.get("ports", []) for r in p["reach"]}, set(cardlib.REACH))
        self.assertEqual({a["level"] for c in loaded.values() for a in c.get("actions", [])}, set(cardlib.LEVELS))
        self.assertEqual({a["choose"] for c in loaded.values() for a in c.get("actions", []) if "choose" in a},
                         set(cardlib.CHOICE_SOURCES))
        self.assertTrue(everything)
        self.assertEqual({k for a in areas for k in a}, {"id", "name", "summary"})
        # Invented, and no address of a real machine in them.
        text = "".join(p.read_text() for p in (ROOT / "examples").rglob("*.toml"))
        self.assertIsNone(re.search(r"\b\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}\b", text))


class NothingHardWired(unittest.TestCase):
    SOURCE = (ROOT / "pihub" / "page" / "app.js").read_text()

    def test_no_card_id_and_no_german_word_in_the_page_script(self):
        for word in ("platten", "aufraeumen", "Ordner", "Datei", "NETZ", "NUMMER"):
            self.assertNotIn(word, self.SOURCE, word)
        self.assertEqual(re.findall(r"<[A-ZÄÖÜ]{3,}>", self.SOURCE), [])  # placeholders come from texts.py
        self.assertEqual(re.findall(r"D\.cards\.[a-z_]+\b(?!\s*[\[(])", self.SOURCE.replace("D.cards.length", "")), [])

    def test_placeholders_exist_in_both_languages(self):
        for key in ("ph_network", "ph_port", "ph_number", "ph_folder", "ph_file", "ph_word", "ph_source", "ph_target",
                    "fc_aside_dir"):
            self.assertIn(key, PAGE["en"])
            self.assertIn(key, PAGE["de"])
            if key != "ph_port":  # the same word in both languages
                self.assertNotEqual(PAGE["en"][key], PAGE["de"][key], key)

    def test_every_section_has_a_name_for_the_sentences(self):
        for name in scan.SECTIONS:
            if name in ("host", "timers", "vitals", "web"):
                continue
            self.assertIn("what_" + name, PAGE["en"], name)
            self.assertIn("what_" + name, PAGE["de"], name)


class Version(unittest.TestCase):
    def test_version(self):
        self.assertEqual(__version__, "0.3.0")
        with contextlib.redirect_stdout(io.StringIO()) as out, self.assertRaises(SystemExit) as stop:
            cli.main(["--version"])
        self.assertEqual(stop.exception.code, 0)
        self.assertIn("0.3.0", out.getvalue())


if __name__ == "__main__":
    unittest.main()
