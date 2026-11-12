"""Run with: python3 -m unittest discover -s tests"""
import json
import stat
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from pihub import history, scan, state  # noqa: E402


def inv(temp=50.0, disk=None):
    return {"sections": {
        "vitals": {"ok": True, "data": {"temp_c": temp, "load1": 0.5, "mem_used_percent": 40, "uptime_s": 9}},
        "storage": {"ok": True, "data": {"usage": [{"target": t, "used_percent": p}
                                                    for t, p in (disk or {"/": 12}).items()]}}}}


def st(**cards):
    return {"cards": {k: {"status": v} for k, v in cards.items()}, "findings": []}


def lines(path):
    return [json.loads(x) for x in path.read_text().splitlines()]


class Vitals(unittest.TestCase):
    def test_fields(self):
        v = scan.vitals()
        self.assertEqual(set(v), {"temp_c", "load1", "mem_used_percent", "uptime_s"})
        self.assertIsInstance(v["load1"], float)
        self.assertIsInstance(v["uptime_s"], int)
        self.assertTrue(0 <= v["mem_used_percent"] <= 100)
        self.assertTrue(v["temp_c"] is None or isinstance(v["temp_c"], float))


class Keys(unittest.TestCase):
    def test_keys_stable_and_distinct(self):
        cards = {"a": {"units": ["x.service", "y.service"], "todo": "t"}}
        r1 = state.build({"sections": {}}, cards)
        r2 = state.build({"sections": {}}, cards)
        keys = [f["key"] for f in r1["findings"]]
        self.assertEqual(keys, [f["key"] for f in r2["findings"]])
        self.assertEqual(keys, ["a:unit_missing:x.service", "a:unit_missing:y.service", "a:todo:"])

    def test_check_key_uses_label(self):
        r = state.build({"sections": {}}, {"a": {"checks": [{"label": "works", "run": "x"}]}},
                        lambda c: {"label": c["label"], "ok": False, "timeout": False, "detail": ""})
        self.assertEqual(r["findings"][0]["key"], "a:check_failed:works")


class Record(unittest.TestCase):
    def setUp(self):
        self._t = tempfile.TemporaryDirectory()
        self.dir = Path(self._t.name)
        self.addCleanup(self._t.cleanup)

    def test_point_and_changes(self):
        history.record(self.dir, inv(disk={"/": 12, "/srv": 80}), st(a="ok", b="ok", new="ok"),
                       st(a="ok", b="problem"), now=1000)
        p = lines(self.dir / "history.jsonl")[0]
        self.assertEqual(p, {"t": 1000, "temp_c": 50.0, "load1": 0.5, "mem": 40,
                             "disk": {"/": 12, "/srv": 80},
                             "status": {"ok": 3, "problem": 0, "todo": 0, "off": 0}})
        self.assertEqual(lines(self.dir / "changes.jsonl"),
                         [{"t": 1000, "card": "b", "from": "problem", "to": "ok"}])

    def test_no_change_no_entry(self):
        history.record(self.dir, inv(), st(a="ok", n="ok"), st(a="ok"), now=1)
        self.assertFalse((self.dir / "changes.jsonl").exists())

    def test_modes(self):
        history.record(self.dir, inv(), st(a="ok"), st(a="off"), now=1)
        for n in ("history.jsonl", "changes.jsonl"):
            self.assertEqual(stat.S_IMODE((self.dir / n).stat().st_mode), 0o600)

    def test_trim(self):
        now = 100 * 86400
        old = [{"t": now - 20 * 86400 + i} for i in range(10)]
        new = [{"t": now - 100 + i} for i in range(4500)]
        (self.dir / "history.jsonl").write_text("".join(json.dumps(r) + "\n" for r in old + new))
        chg = [{"t": i, "card": "a", "from": "ok", "to": "off"} for i in range(600)]
        (self.dir / "changes.jsonl").write_text("".join(json.dumps(r) + "\n" for r in chg))
        history.record(self.dir, inv(), st(a="ok"), st(a="ok"), now=now)
        h = lines(self.dir / "history.jsonl")
        self.assertEqual(len(h), 4501)
        self.assertTrue(all(r["t"] >= now - 14 * 86400 for r in h))
        c = lines(self.dir / "changes.jsonl")
        self.assertEqual((len(c), c[0]["t"]), (500, 100))
        self.assertEqual(stat.S_IMODE((self.dir / "history.jsonl").stat().st_mode), 0o600)

    def test_no_rewrite_below_threshold(self):
        rows = [{"t": 1 + i} for i in range(10)]  # all older than 14 days at now
        (self.dir / "history.jsonl").write_text("".join(json.dumps(r) + "\n" for r in rows))
        history.record(self.dir, inv(), st(a="ok"), {}, now=50 * 86400)
        self.assertEqual(len(lines(self.dir / "history.jsonl")), 11)


class View(unittest.TestCase):
    def setUp(self):
        self._t = tempfile.TemporaryDirectory()
        self.dir = Path(self._t.name)
        self.addCleanup(self._t.cleanup)

    def test_empty(self):
        self.assertEqual(history.view(self.dir, now=5), {"day": [], "week": [], "changes": []})

    def test_thinning_keeps_last(self):
        now = 10 * 86400
        rows = [{"t": now - 86400 + 1 + i * 10} for i in range(8000)]
        rows = [r for r in rows if r["t"] <= now]
        (self.dir / "history.jsonl").write_text("".join(json.dumps(r) + "\n" for r in rows))
        v = history.view(self.dir, now=now)
        self.assertLessEqual(len(v["day"]), 300)
        self.assertEqual(v["day"][-1], rows[-1])
        self.assertLessEqual(len(v["week"]), 300)

    def test_ranges_and_changes_order(self):
        now = 10 * 86400
        (self.dir / "history.jsonl").write_text(
            "".join(json.dumps({"t": now - s}) + "\n" for s in (3 * 86400, 3600, 0)))
        (self.dir / "changes.jsonl").write_text(
            "".join(json.dumps({"t": i, "card": "a", "from": "ok", "to": "off"}) + "\n" for i in range(60)))
        v = history.view(self.dir, now=now)
        self.assertEqual(len(v["day"]), 2)
        self.assertEqual(len(v["week"]), 3)
        self.assertEqual(len(v["changes"]), 50)
        self.assertEqual(v["changes"][0]["t"], 59)

    def test_broken_line_skipped(self):
        (self.dir / "history.jsonl").write_text('{"t": 5}\nnot json\n{"t": 6}\n')
        self.assertEqual([r["t"] for r in history.view(self.dir, now=10)["day"]], [5, 6])


if __name__ == "__main__":
    unittest.main()
