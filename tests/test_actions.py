"""Run with: python3 -m unittest discover -s tests

Only invented cards with echo, true, false, sleep, printf and cat in them.
"""
import json
import os
import shutil
import stat
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from pihub import actions, auth, cards, mutes  # noqa: E402

AREAS = '[[areas]]\nid = "net"\nname = "Network"\n'
CARD = '''id = "demo"
name = "Demo"
area = "net"
summary = "An invented card"
about = "Only for tests."

[[actions]]
label = "Say hello"
run = "echo hello"
level = "read"

[[actions]]
label = "Change something"
run = "echo changed"
level = "change"
effect = "Nothing."

[[actions]]
label = "Copy only"
run = "echo never"
level = "copy"
effect = "Nothing."

[[actions]]
label = "Pick an address"
run = "echo got {value}"
level = "read"
choose = "blocked_addresses"

[[actions]]
label = "Slow"
run = "sleep 2"
level = "read"
timeout = 5

[[actions]]
label = "Stray placeholder"
run = "echo {value}"
level = "read"

[[actions]]
label = "Unknown source"
run = "echo {value}"
level = "read"
choose = "everything"
'''
LIST = ["1.2.3.4", "5.6.7.8", "1.2.3.4; touch x", "9.9.9.9\n", "-v", "--help"]
CHOICES = {"blocked_addresses": lambda: LIST, "unmounted_targets": lambda: ["/mnt/usb"]}


def make_data(card=CARD):
    d = Path(tempfile.mkdtemp())
    (d / "cards").mkdir()
    (d / "areas.toml").write_text(AREAS)
    (d / "cards" / "demo.toml").write_text(card)
    return d


def alive(pid):
    try:
        return Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()[0] != "Z"
    except OSError:
        return False


class Resolve(unittest.TestCase):
    def setUp(self):
        self.dir = make_data()
        self.addCleanup(shutil.rmtree, self.dir)

    def resolve(self, index, value=None, card="demo"):
        return actions.resolve(self.dir, card, index, value, CHOICES)

    def refused(self, key, *args, **kw):
        with self.assertRaises(actions.ActionError) as cm:
            self.resolve(*args, **kw)
        self.assertEqual(cm.exception.key, key)

    def test_plain_action(self):
        plan = self.resolve(0)
        self.assertEqual(plan, {"command": "echo hello", "label": "Say hello", "level": "read", "timeout": 120})
        self.assertEqual(self.resolve(1)["level"], "change")
        self.assertEqual(self.resolve(4)["timeout"], 5)

    def test_copy_is_refused(self):
        self.refused("level_copy", 2)

    def test_unknown_card_and_index(self):
        self.refused("unknown_action", 0, card="nope")
        self.refused("unknown_action", 99)
        self.refused("unknown_action", -1)
        self.refused("unknown_action", "0")
        self.refused("unknown_action", True)
        self.refused("unknown_action", 0, card=["demo"])
        self.refused("unknown_action", 0, card="../demo")

    def test_value_only_from_the_list(self):
        self.assertEqual(self.resolve(3, "5.6.7.8")["command"], "echo got 5.6.7.8")
        self.refused("bad_value", 3, "4.4.4.4")  # well-formed, but not on the list
        self.refused("bad_value", 3, None)
        self.refused("bad_value", 3, "")
        self.refused("bad_value", 3, 12)

    def test_shell_characters_never_pass(self):
        # Even though the list itself contains it: the pattern refuses it.
        for value in ("1.2.3.4; touch x", "9.9.9.9\n", "$(touch x)", "a b", "`id`", "a'b", "x|y"):
            self.refused("bad_value", 3, value)
        self.assertNotIn("1.2.3.4; touch x", actions.allowed_values("blocked_addresses", CHOICES))
        self.assertNotIn("9.9.9.9\n", actions.allowed_values("blocked_addresses", CHOICES))

    def test_value_may_not_start_with_a_dash(self):
        for value in ("-v", "--help", "-1.2.3.4"):
            self.refused("bad_value", 3, value)
        self.assertEqual([v for v in actions.allowed_values("blocked_addresses", CHOICES) if v.startswith("-")], [])
        self.assertEqual(self.resolve(3, "5.6.7.8")["command"], "echo got 5.6.7.8")

    def test_value_without_choose_is_refused(self):
        self.refused("bad_value", 0, "x")
        self.assertEqual(self.resolve(0, "")["command"], "echo hello")

    def test_inconsistent_card_is_refused(self):
        self.refused("unknown_action", 5)  # {value} without choose
        self.refused("unknown_action", 6, "x")  # choose names no known source

    def test_card_is_read_fresh(self):
        (self.dir / "cards" / "demo.toml").write_text(CARD.replace("echo hello", "echo again"))
        self.assertEqual(self.resolve(0)["command"], "echo again")

    def test_bad_timeout_falls_back(self):
        (self.dir / "cards" / "demo.toml").write_text(CARD.replace("timeout = 5", "timeout = 99999"))
        self.assertEqual(self.resolve(4)["timeout"], 120)

    def test_choices_for(self):
        self.assertEqual(actions.choices_for(self.dir, "demo", 3, CHOICES), ["1.2.3.4", "5.6.7.8"])
        self.assertEqual(actions.choices_for(self.dir, "demo", 0, CHOICES), [])
        with self.assertRaises(actions.ActionError):
            actions.choices_for(self.dir, "demo", 2, CHOICES)

    def test_sources_agree_with_card_format(self):
        self.assertEqual(set(actions.CHOICES), set(cards.CHOICE_SOURCES))

    def test_real_sources_survive_scan_trouble(self):
        with mock.patch.object(actions.scan, "blocks", side_effect=actions.scan.Unavailable("no")), \
                mock.patch.object(actions.scan, "storage", side_effect=actions.scan.Unavailable("no")):
            self.assertEqual(actions.blocked_addresses(), [])
            self.assertEqual(actions.unmounted_targets(), [])

    def test_real_sources_read_the_scan(self):
        with mock.patch.object(actions.scan, "blocks", return_value={"decisions": [{"value": "1.1.1.1"}]}), \
                mock.patch.object(actions.scan, "storage", return_value={"planned": [
                    {"target": "/a", "mounted": True}, {"target": "/b", "mounted": False}]}):
            self.assertEqual(actions.blocked_addresses(), ["1.1.1.1"])
            self.assertEqual(actions.unmounted_targets(), ["/b"])


class Execute(unittest.TestCase):
    def test_exit_code_and_output(self):
        r = actions.execute("echo out; echo err >&2; false", 10)
        self.assertEqual(r["exit"], 1)
        self.assertFalse(r["timed_out"])
        self.assertEqual(sorted(r["output"].split()), ["err", "out"])
        self.assertFalse(r["truncated"])
        self.assertEqual(actions.execute("true", 10)["exit"], 0)

    def test_stdin_is_closed(self):
        self.assertEqual(actions.execute("cat; echo done", 5)["output"], "done\n")

    def test_long_output_is_cut_at_the_end(self):
        r = actions.execute("i=0; while [ $i -lt 3000 ]; do printf 'line%05d\\n' $i; i=$((i+1)); done", 20)
        self.assertEqual(len(r["output"]), 20000)
        self.assertTrue(r["truncated"])
        self.assertTrue(r["output"].endswith("line02999\n"))

    def test_timeout_ends_children_too(self):
        r = actions.execute("sleep 30 & echo $!; wait", 1)
        self.assertTrue(r["timed_out"])
        self.assertIsNone(r["exit"])
        pid = int(r["output"].split()[0])
        for _ in range(20):
            if not alive(pid):
                break
            time.sleep(0.1)
        self.assertFalse(alive(pid))
        self.assertLess(r["seconds"], 5)

    def test_timeout_escalates_to_kill(self):
        with mock.patch.object(actions, "KILL_GRACE", 1):
            r = actions.execute("trap '' TERM; sleep 30 & echo $!; wait", 1)
        self.assertTrue(r["timed_out"])
        pid = int(r["output"].split()[0])
        for _ in range(20):
            if not alive(pid):
                break
            time.sleep(0.1)
        self.assertFalse(alive(pid))

    def test_background_holder_does_not_hang_the_answer(self):
        started = time.monotonic()
        r = actions.execute("sleep 3 & echo main-done", 10)
        self.assertEqual(r["exit"], 0)
        self.assertLess(time.monotonic() - started, 2.9)


class Log(unittest.TestCase):
    def setUp(self):
        self.dir = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.dir)

    def test_append_and_read_newest_first(self):
        for n in range(5):
            actions.log_append(self.dir, {"event": "action", "n": n})
        self.assertEqual([e["n"] for e in actions.log_read(self.dir, 3)], [4, 3, 2])
        self.assertEqual(stat.S_IMODE(os.stat(self.dir / "private" / "actions.log").st_mode), 0o600)

    def test_private_folder(self):
        actions.log_append(self.dir, {"n": 1})
        self.assertEqual(stat.S_IMODE(os.stat(self.dir / "private").st_mode), 0o700)
        self.assertFalse((self.dir / "actions.log").exists())

    def test_log_is_cut_back(self):
        with mock.patch.object(actions, "LOG_KEEP", 5), mock.patch.object(actions, "LOG_CUT_AT", 8):
            lines = []
            for n in range(30):
                actions.log_append(self.dir, {"n": n})
                lines.append(len((self.dir / "private" / "actions.log").read_text().splitlines()))
            self.assertLessEqual(max(lines), 8)  # never more than the limit
            self.assertIn(5, lines)  # it was cut back to the kept number
            self.assertEqual([e["n"] for e in actions.log_read(self.dir, 3)], [29, 28, 27])
            self.assertEqual(stat.S_IMODE(os.stat(self.dir / "private" / "actions.log").st_mode), 0o600)
            self.assertEqual(len(actions.log_read(self.dir, 100)), len((self.dir / "private" / "actions.log").read_text().splitlines()))

    def test_log_cut_counts_an_existing_file(self):
        (self.dir / "private").mkdir()
        (self.dir / "private" / "actions.log").write_text("".join(json.dumps({"n": n}) + "\n" for n in range(9)))
        with mock.patch.object(actions, "LOG_KEEP", 4), mock.patch.object(actions, "LOG_CUT_AT", 9):
            actions.log_append(self.dir, {"n": 9})  # 10 lines: cut
        self.assertEqual([e["n"] for e in actions.log_read(self.dir, 100)], [9, 8, 7, 6])

    def test_missing_and_damaged(self):
        self.assertEqual(actions.log_read(self.dir, 5), [])
        (self.dir / "private").mkdir()
        (self.dir / "private" / "actions.log").write_text('{"n": 1}\nnot json\n{"n": 2}\n')
        self.assertEqual([e["n"] for e in actions.log_read(self.dir, 5)], [2, 1])


class Auth(unittest.TestCase):
    def setUp(self):
        self.dir = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.dir)

    def test_set_and_verify(self):
        self.assertFalse(auth.password_is_set(self.dir))
        self.assertFalse(auth.verify(self.dir, "anything long"))
        auth.set_password(self.dir, "correct horse")
        self.assertTrue(auth.password_is_set(self.dir))
        self.assertTrue(auth.verify(self.dir, "correct horse"))
        self.assertFalse(auth.verify(self.dir, "correct horsf"))
        self.assertFalse(auth.verify(self.dir, ""))
        self.assertFalse(auth.verify(self.dir, None))
        path = self.dir / "private" / "login.json"
        self.assertEqual(stat.S_IMODE(os.stat(path).st_mode), 0o600)
        self.assertEqual(stat.S_IMODE(os.stat(path.parent).st_mode), 0o700)
        self.assertFalse((self.dir / "login.json").exists())
        text = path.read_text()
        self.assertNotIn("correct horse", text)
        entry = json.loads(text)
        self.assertEqual((entry["scheme"], entry["n"], entry["r"], entry["p"], entry["dklen"]), ("scrypt", 2**14, 8, 1, 32))

    def test_salt_differs(self):
        auth.set_password(self.dir, "correct horse")
        first = json.loads((self.dir / "private" / "login.json").read_text())
        auth.set_password(self.dir, "correct horse")
        second = json.loads((self.dir / "private" / "login.json").read_text())
        self.assertNotEqual(first["salt"], second["salt"])
        self.assertNotEqual(first["hash"], second["hash"])

    def test_too_short(self):
        with self.assertRaises(auth.PasswordError) as cm:
            auth.set_password(self.dir, "short")
        self.assertEqual(cm.exception.key, "password_short")
        self.assertFalse(auth.password_is_set(self.dir))

    def test_damaged_file_never_verifies(self):
        auth.private_dir(self.dir)
        (self.dir / "private" / "login.json").write_text("{not json")
        self.assertFalse(auth.verify(self.dir, "whatever long"))
        (self.dir / "private" / "login.json").write_text(json.dumps({"scheme": "scrypt", "n": 2**30, "r": 8, "p": 1,
                                                         "dklen": 32, "salt": "00", "hash": "00"}))
        self.assertFalse(auth.verify(self.dir, "whatever long"))

    def test_throttle(self):
        now = [1000.0]
        t = auth.Throttle(clock=lambda: now[0])
        for _ in range(4):
            t.failed()
        self.assertEqual(t.remaining(), 0)
        t.failed()
        self.assertEqual(t.remaining(), 60)
        now[0] += 59
        self.assertGreater(t.remaining(), 0)
        now[0] += 2
        self.assertEqual(t.remaining(), 0)
        locks = []
        for _ in range(5):  # series 2..6
            for _ in range(5):
                t.failed()
            locks.append(t.remaining())
            now[0] += 5000
        self.assertEqual(locks, [120, 240, 480, 900, 900])
        t.succeeded()
        for _ in range(4):
            t.failed()
        self.assertEqual(t.remaining(), 0)
        t.failed()
        self.assertEqual(t.remaining(), 60)

    def test_sessions_expire(self):
        now = [0.0]
        s = auth.Sessions(clock=lambda: now[0])
        token = s.login()
        other = s.login()
        self.assertNotEqual(token, other)
        self.assertGreaterEqual(len(token), 43)
        self.assertTrue(s.valid(token))
        self.assertFalse(s.valid("nonsense"))
        self.assertFalse(s.valid(None))
        now[0] = 12 * 3600 - 1
        self.assertTrue(s.valid(token))
        now[0] = 12 * 3600
        self.assertFalse(s.valid(token))
        self.assertEqual(s._until, {})  # expired tokens are removed
        fresh = s.login()
        s.logout(fresh)
        self.assertFalse(s.valid(fresh))


class Mutes(unittest.TestCase):
    def setUp(self):
        self.dir = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.dir)

    def test_mute_and_unmute(self):
        self.assertEqual(mutes.load(self.dir), {})
        mutes.mute(self.dir, "demo:port_differs", "  known and fine  ")
        data = mutes.load(self.dir)
        self.assertEqual(data["demo:port_differs"]["reason"], "known and fine")
        self.assertIn("since", data["demo:port_differs"])
        self.assertEqual(stat.S_IMODE(os.stat(self.dir / "muted.json").st_mode), 0o600)
        mutes.unmute(self.dir, "demo:port_differs")
        self.assertEqual(mutes.load(self.dir), {})
        mutes.unmute(self.dir, "never-there")

    def test_bad_input(self):
        for key, reason, error in (("", "fine reason", "bad_key"), ("k" * 201, "fine reason", "bad_key"),
                                   (5, "fine reason", "bad_key"), ("k", "  ab  ", "bad_reason"),
                                   ("k", "x" * 301, "bad_reason"), ("k", None, "bad_reason")):
            with self.assertRaises(mutes.MuteError) as cm:
                mutes.mute(self.dir, key, reason)
            self.assertEqual(cm.exception.key, error)
        self.assertFalse((self.dir / "muted.json").exists())


if __name__ == "__main__":
    unittest.main()
