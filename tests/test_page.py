"""Run with: python3 -m unittest tests.test_page

Builds a small invented data folder, asks payload() for the page data and lets
tests/page_smoke.mjs draw every page with it in a stand-in for the browser.
Only invented cards; the smoke test needs `node` and is skipped without it.
"""
import json
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from pihub import cards as cardlib, history, mutes, state, web  # noqa: E402

AREAS = '''[[areas]]
id = "web"
name = "Web"
summary = "Pages"

[[areas]]
id = "sys"
name = "System"
'''
CARDS = {
    "site": '''id = "site"
name = "Site"
area = "web"
summary = "An invented site"
about = "Only for tests."
needs = ["base"]

[[checks]]
label = "Answers"
run = "false"

[[actions]]
label = "Say hello"
run = "echo hello"
level = "read"

[[actions]]
label = "Restart site"
run = "echo restarted"
level = "change"
effect = "The site is gone for a moment."

[[actions]]
label = "Copy me"
run = "echo never"
level = "copy"
effect = "Nothing."
''',
    "base": '''id = "base"
name = "Base"
area = "sys"
summary = "An invented base"
about = "Only for tests."
todo = "Something is still open."
''',
    "guard": '''id = "guard"
name = "Guard"
area = "sys"
summary = "An invented guard"
about = "Only for tests."
needs = ["base"]

[[actions]]
label = "Unlock an address"
run = "echo unlock {value}"
level = "change"
choose = "blocked_addresses"
effect = "The address may connect again."
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


def section(data):
    return {"ok": True, "data": data}


def inventory(temp, load, mem):
    return {"taken_at": "2026-11-12T10:00:00+0100", "sections": {
        "host": section({"name": "testhost", "model": "Test Pi"}),
        "units": section({}), "containers": section([]),
        "ports": section({"listening": []}),
        "firewall": section({"tool": "ufw", "status": "active", "default": "deny (incoming)", "rules": []}),
        "blocks": section({"tool": "crowdsec", "decisions": [
            {"value": "192.0.2.7", "reason": "invented", "remaining": "1h", "country": "XX"}]}),
        "storage": section({"devices": [
            {"name": "sda", "type": "disk", "parent": None, "mounted_at": [], "size": "100G", "model": "Invented", "transport": "usb"},
            {"name": "sda1", "type": "part", "parent": "sda", "mounted_at": ["/"], "size": "100G", "fstype": "ext4", "label": "root"}],
            "planned": [
            {"target": "/mnt/spare", "mounted": False}, {"target": "/", "mounted": True}],
            "usage": [{"target": "/", "used_percent": 41}, {"target": "/mnt/data", "used_percent": 70}]}),
        "vitals": section({"temp_c": temp, "load1": load, "mem_used_percent": mem}),
        "home": section({"path": "/home/test", "entries": []}),
    }}


def make_data(root: Path, now: int) -> Path:
    data = root / "data"
    (data / "cards").mkdir(parents=True)
    (data / "areas.toml").write_text(AREAS)
    for cid, text in CARDS.items():
        (data / "cards" / f"{cid}.toml").write_text(text)
    _, loaded, problems = cardlib.load(data)
    assert not problems, problems
    inv = inventory(48.5, 0.4, 35)
    (data / "inventory.json").write_text(json.dumps(inv))
    fail = lambda check: {"label": check["label"], "ok": False, "timeout": False, "detail": "no answer"}  # noqa: E731
    current = state.build(inv, loaded, check_runner=fail)
    (data / "state.json").write_text(json.dumps(current))
    # A week of readings, one gap in the temperature, and a change of state.
    older = {"cards": {"site": {"status": "ok"}}, "findings": []}
    for i in range(30):
        point = inventory(None if i == 12 else 45 + i % 7, 0.2 + i / 50, 30 + i % 5)
        history.record(data, point, current, older if i == 29 else current, now=now - (30 - i) * 300)
    history.record(data, inventory(47.0, 0.3, 34), current, older, now=now - 60)
    muted_key = next(f["key"] for f in current["findings"] if f["kind"] == "todo")
    mutes.mute(data, muted_key, "known for now")
    return data


class Payload(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp)
        self.data = make_data(self.tmp, int(time.time()))

    def test_payload_carries_history_vitals_and_muted_findings(self):
        p = web.payload(self.data, "de")
        self.assertGreaterEqual(len(p["history"]["day"]), 2)
        self.assertIn("changes", p["history"])
        self.assertEqual(p["vitals"]["temp_c"], 48.5)
        todo = next(f for f in p["state"]["findings"] if f["kind"] == "todo")
        self.assertTrue(todo["muted"])
        self.assertEqual(todo["reason"], "known for now")
        self.assertEqual(p["state"]["cards"]["base"]["status"], "ok")   # only a silenced todo was left
        self.assertEqual(p["state"]["cards"]["site"]["status"], "problem")

    def test_page_script_parses_and_smoke_test_passes(self):
        node = shutil.which("node")
        if not node:
            self.skipTest("node is not installed")
        out = self.tmp / "payload.json"
        out.write_text(json.dumps(web.payload(self.data, "de")))
        out_en = self.tmp / "payload-en.json"
        out_en.write_text(json.dumps(web.payload(self.data, "en")))
        for path in (out, out_en):
            run = subprocess.run([node, str(ROOT / "tests" / "page_smoke.mjs"), str(path)],
                                 capture_output=True, text=True, timeout=120)
            self.assertEqual(run.returncode, 0, run.stdout + run.stderr)
            self.assertIn("smoke test: ok", run.stdout)

    def test_every_text_the_page_asks_for_exists_in_both_languages(self):
        import re
        from pihub.texts import PAGE, TEXTS
        source = (ROOT / "pihub" / "page" / "app.js").read_text()
        keys = {k for k in re.findall(r"\bt\('([a-z0-9_]+)'", source) if not k.endswith("_")}
        for lang in ("en", "de"):
            known = {**TEXTS["en"], **PAGE["en"], **TEXTS[lang], **PAGE[lang]}
            self.assertEqual(sorted(k for k in keys if k not in known), [], lang)
        self.assertEqual(set(PAGE["en"]), set(PAGE["de"]))


if __name__ == "__main__":
    unittest.main()
