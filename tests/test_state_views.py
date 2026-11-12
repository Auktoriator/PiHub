"""Run with: python3 -m unittest discover -s tests"""
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from pihub import state, views  # noqa: E402


def inventory(units=None, containers=None, listening=None, rules=None):
    return {"sections": {
        "units": {"ok": True, "data": units or {}},
        "containers": {"ok": True, "data": containers or []},
        "ports": {"ok": True, "data": {"listening": listening or []}},
        "firewall": {"ok": True, "data": {"tool": "ufw", "status": "active", "default": "deny (incoming)",
                                          "rules": rules or []}},
    }}


def unit(active="active", enabled="enabled"):
    return {"active": active, "enabled": enabled, "own": True}


def listen(port, bound, process="x", proto="tcp"):
    return {"proto": proto, "port": port, "bound": bound, "address": "", "processes": [process]}


def rule(to, action, source):
    return {"to": to, "action": action, "from": source, "comment": ""}


PASS = lambda check: {"label": check["label"], "ok": True, "timeout": False, "detail": ""}  # noqa: E731
FAIL = lambda check: {"label": check["label"], "ok": False, "timeout": False, "detail": "no"}  # noqa: E731


class State(unittest.TestCase):
    def test_switched_off_is_not_a_problem(self):
        cards = {"game": {"units": ["game.service"], "switch": "all.target",
                          "checks": [{"label": "runs", "run": "false"}]}}
        inv = inventory({"all.target": unit("inactive"), "game.service": unit("inactive")})
        result = state.build(inv, cards, FAIL)
        self.assertEqual(result["cards"]["game"]["status"], "off")
        self.assertEqual(result["findings"], [])

    def test_unit_states(self):
        cards = {"a": {"units": ["run.service", "job.service", "down.service", "gone.service", "bad.service"]}}
        inv = inventory({"run.service": unit(), "job.service": unit("inactive", "static"),
                         "down.service": unit("inactive"), "bad.service": unit("failed")})
        result = state.build(inv, cards, PASS)
        self.assertEqual(result["cards"]["a"]["units"], {
            "run.service": "running", "job.service": "idle", "down.service": "stopped",
            "gone.service": "missing", "bad.service": "failed"})
        self.assertEqual(sorted(f["kind"] for f in result["findings"]),
                         ["unit_failed", "unit_missing", "unit_stopped"])

    def test_failed_check_and_todo(self):
        cards = {"a": {"checks": [{"label": "works", "run": "x"}]}, "b": {"todo": "set it up"}}
        result = state.build(inventory(), cards, FAIL)
        self.assertEqual(result["cards"]["a"]["status"], "problem")
        self.assertEqual(result["cards"]["b"]["status"], "todo")
        self.assertEqual([f["kind"] for f in result["findings"]], ["check_failed", "todo"])

    def test_port_bound_to_the_machine_only_is_a_finding(self):
        cards = {"share": {"ports": [{"port": 445, "reach": ["lan"], "why": "w"}]}}
        result = state.build(inventory(listening=[listen(445, "local")]), cards, PASS)
        self.assertEqual([f["kind"] for f in result["findings"]], ["port_only_local"])

    def test_real_check_runs_and_keeps_output_only_on_failure(self):
        self.assertEqual(state.run_check({"label": "l", "run": "echo hi"})["detail"], "")
        failed = state.run_check({"label": "l", "run": "echo hi", "expect": "bye"})
        self.assertFalse(failed["ok"])
        self.assertEqual(failed["detail"], "hi")
        shown = state.run_check({"label": "l", "run": "echo 21 degrees; echo more", "show": True})
        self.assertEqual((shown["ok"], shown["detail"]), (True, "21 degrees"))


class Security(unittest.TestCase):
    RULES = [rule("22/tcp", "ALLOW IN", "10.0.0.0/24"), rule("22/tcp on tailscale0", "ALLOW IN", "Anywhere"),
             rule("443/tcp", "ALLOW IN", "Anywhere"), rule("443/tcp (v6)", "ALLOW IN", "Anywhere (v6)"),
             rule("25575/tcp", "DENY IN", "Anywhere"), rule("Anywhere", "DENY IN", "203.0.113.9")]

    def rows(self, cards, listening):
        result = views.security(inventory(listening=listening, rules=self.RULES), cards)
        return {r["port"]: r for r in result["ports"]}, result

    def test_firewall_groups(self):
        self.assertEqual(views.firewall_for(22, "tcp", self.RULES)[0], ["lan", "tailscale"])
        self.assertEqual(views.firewall_for(443, "tcp", self.RULES)[0], ["internet"])
        self.assertEqual(views.firewall_for(25575, "tcp", self.RULES)[0], [])
        self.assertEqual(views.firewall_for(9999, "tcp", self.RULES), ([], []))

    def test_verdicts(self):
        cards = {
            "ssh": {"ports": [{"port": 22, "reach": ["lan", "tailscale"], "why": "w"}]},
            "web": {"ports": [{"port": 443, "reach": ["lan"], "why": "w"}]},
            "rcon": {"ports": [{"port": 25575, "reach": ["local"], "why": "w"}]},
            "share": {"ports": [{"port": 445, "reach": ["lan"], "why": "w"}]},
            "gone": {"ports": [{"port": 7000, "reach": ["lan"], "why": "w"}]},
        }
        rows, result = self.rows(cards, [listen(22, "all"), listen(443, "all"), listen(25575, "all"),
                                         listen(445, "local"), listen(8000, "all"), listen(40000, "all")])
        self.assertEqual({p: r["verdict"] for p, r in rows.items()}, {
            22: "as_meant", 443: "differs", 25575: "as_meant", 445: "differs", 7000: "not_listening", 8000: "no_card"})
        self.assertTrue(rows[25575]["firewall_only"])
        self.assertFalse(rows[22]["firewall_only"])
        self.assertEqual(result["bans"], [{"address": "203.0.113.9", "comment": ""}])

    def test_docker_published_port_is_flagged(self):
        rows, _ = self.rows({}, [listen(8000, "all", "docker-proxy")])
        self.assertTrue(rows[8000]["docker"])


class Files(unittest.TestCase):
    def test_home_entries_get_their_places(self):
        inv = {"sections": {"home": {"ok": True, "data": {"path": "/home/x", "entries": [
            {"name": "game", "kind": "dir", "git": False}, {"name": "gamer", "kind": "dir", "git": False}]}}}}
        cards = {"g": {"places": [{"path": "~/game/world", "what": "the world"}, {"path": "/etc/x", "what": "settings"}]}}
        result = views.files(inv, cards)
        self.assertEqual([len(e["places"]) for e in result["entries"]], [1, 0])
        self.assertEqual([p["path"] for p in result["elsewhere"]], ["/etc/x"])


class ApplyMutes(unittest.TestCase):
    def base(self):
        return {"taken_at": "x", "cards": {
            "a": {"status": "problem", "units": {}}, "b": {"status": "problem"}, "c": {"status": "todo"},
            "d": {"status": "off"}, "e": {"status": "ok"}},
            "findings": [
                {"card": "a", "key": "a:unit_failed:x.service", "kind": "unit_failed", "name": "x.service"},
                {"card": "a", "key": "a:todo:", "kind": "todo", "text": "t"},
                {"card": "b", "key": "b:check_failed:y", "kind": "check_failed"},
                {"card": "b", "key": "b:port_differs:80", "kind": "port_differs"},
                {"card": "c", "key": "c:todo:", "kind": "todo", "text": "t"},
                {"card": None, "key": "undescribed", "kind": "undescribed", "items": []}]}

    def test_nothing_muted_keeps_status(self):
        out = views.apply_mutes(self.base(), {})
        self.assertEqual({k: v["status"] for k, v in out["cards"].items()},
                         {"a": "problem", "b": "problem", "c": "todo", "d": "off", "e": "ok"})
        self.assertFalse(any(f.get("muted") for f in out["findings"]))

    def test_muted_finding_is_marked_with_reason_and_date(self):
        muted = {"a:unit_failed:x.service": {"reason": "known", "since": "2026-11-01T10:00:00+0100"}}
        out = views.apply_mutes(self.base(), muted)
        f = out["findings"][0]
        self.assertTrue(f["muted"])
        self.assertEqual((f["reason"], f["since"]), ("known", "2026-11-01T10:00:00+0100"))
        self.assertNotIn("muted", out["findings"][2])

    def test_card_falls_back_to_todo_when_only_the_todo_is_left(self):
        out = views.apply_mutes(self.base(), {"a:unit_failed:x.service": {"reason": "known", "since": "s"}})
        self.assertEqual(out["cards"]["a"]["status"], "todo")

    def test_card_stays_problem_while_one_finding_counts(self):
        out = views.apply_mutes(self.base(), {"b:check_failed:y": {"reason": "known", "since": "s"}})
        self.assertEqual(out["cards"]["b"]["status"], "problem")
        out = views.apply_mutes(self.base(), {"b:check_failed:y": {"reason": "k", "since": "s"},
                                              "b:port_differs:80": {"reason": "k", "since": "s"}})
        self.assertEqual(out["cards"]["b"]["status"], "ok")

    def test_silenced_todo_gives_ok_and_off_stays_off(self):
        out = views.apply_mutes(self.base(), {"c:todo:": {"reason": "later", "since": "s"}})
        self.assertEqual(out["cards"]["c"]["status"], "ok")
        self.assertEqual(out["cards"]["d"]["status"], "off")

    def test_findings_without_a_card_do_not_touch_card_status(self):
        out = views.apply_mutes(self.base(), {"undescribed": {"reason": "fine", "since": "s"}})
        self.assertTrue(out["findings"][-1]["muted"])
        self.assertEqual(out["cards"]["e"]["status"], "ok")

    def test_input_is_not_changed_and_bad_mute_data_is_ignored(self):
        before = self.base()
        views.apply_mutes(before, {"a:todo:": {"reason": "r", "since": "s"}})
        self.assertEqual(before, self.base())
        out = views.apply_mutes(before, {"a:unit_failed:x.service": "nonsense"})
        self.assertFalse(out["findings"][0].get("muted"))
        self.assertEqual(views.apply_mutes(before, None)["cards"]["a"]["status"], "problem")


if __name__ == "__main__":
    unittest.main()
