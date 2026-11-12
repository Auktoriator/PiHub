"""Runner and web routes, end to end over real sockets in a temp folder.

Only invented cards with echo, sleep and the like; the scan and the state
collection are replaced, so nothing here touches the real machine.
"""
import http.client
import json
import os
import shutil
import socket
import stat
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from pihub import auth, mutes, runner, web  # noqa: E402
from tests.test_actions import CARD, CHOICES, make_data  # noqa: E402

PASSWORD = "correct horse"
web.Handler.log_message = lambda *args: None  # keep the test output readable


class Base(unittest.TestCase):
    def setUp(self):
        self.dir = make_data()
        self.sock = os.path.join(tempfile.mkdtemp(prefix="ph"), "r.sock")
        self.addCleanup(shutil.rmtree, self.dir)
        self.addCleanup(shutil.rmtree, os.path.dirname(self.sock))
        self.collected = []
        auth.set_password(self.dir, PASSWORD)
        self.server = runner.make_server(self.dir, self.sock, choices=CHOICES,
                                         collect=lambda d: self.collected.append(d))
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)

    def call(self, request, timeout=20):
        return runner.call(self.sock, request, timeout)

    def login(self):
        reply = self.call({"op": "login", "password": PASSWORD, "peer": "10.0.0.9"})
        self.assertTrue(reply["ok"], reply)
        return reply["token"]


class RunnerTests(Base):
    def test_socket_is_private(self):
        self.assertEqual(stat.S_IMODE(os.stat(self.sock).st_mode), 0o600)

    def test_status_needs_no_token(self):
        self.assertEqual(self.call({"op": "status"}), {"ok": True, "password_set": True})

    def test_everything_else_needs_a_session(self):
        for req in ({"op": "run", "card": "demo", "action": 0}, {"op": "choices", "card": "demo", "action": 3},
                    {"op": "log"}, {"op": "mute", "key": "k", "reason": "because"}, {"op": "unmute", "key": "k"}):
            for token in (None, "forged"):
                self.assertEqual(self.call({**req, "token": token}), {"ok": False, "error": "not_logged_in"})
        self.assertFalse((self.dir / "private" / "actions.log").exists() and "action" in (self.dir / "private" / "actions.log").read_text())
        self.assertEqual(self.call({"op": "session", "token": "forged"}), {"ok": True, "logged_in": False})

    def test_bad_requests(self):
        self.assertEqual(self.call({"op": "format-disk"})["error"], "bad_request")
        self.assertEqual(self.call({"nothing": 1})["error"], "bad_request")
        for raw in (b"not json\n", b"[1]\n", b"\xff\xfe\n", b"{}"):
            s = socket.socket(socket.AF_UNIX)
            s.connect(self.sock)
            s.sendall(raw)
            s.shutdown(socket.SHUT_WR)
            reply = json.loads(s.makefile().readline())
            s.close()
            self.assertEqual(reply["error"], "bad_request", raw)
        big = socket.socket(socket.AF_UNIX)
        big.connect(self.sock)
        try:
            big.sendall(b'{"op":"status","x":"' + b"a" * 70000 + b'"}\n')
        except OSError:
            pass
        self.assertEqual(json.loads(big.makefile().readline())["error"], "bad_request")
        big.close()
        self.assertTrue(self.call({"op": "status"})["ok"])  # still alive

    def test_login_logout_session(self):
        reply = self.call({"op": "login", "password": "wrong password", "peer": "10.0.0.9"})
        self.assertEqual(reply, {"ok": False, "error": "wrong_password"})
        token = self.login()
        self.assertEqual(self.call({"op": "session", "token": token})["logged_in"], True)
        self.call({"op": "logout", "token": token})
        self.assertEqual(self.call({"op": "session", "token": token})["logged_in"], False)
        events = [(e["event"], e["peer"]) for e in self.call({"op": "log", "token": self.login()})["entries"]]
        self.assertEqual(events, [("login", "10.0.0.9"), ("login", "10.0.0.9"), ("login_failed", "10.0.0.9")])

    def test_new_password_ends_all_sessions(self):
        token = self.login()
        self.assertTrue(self.call({"op": "log", "token": token})["ok"])
        auth.set_password(self.dir, "another horse")
        self.assertEqual(self.call({"op": "log", "token": token})["error"], "not_logged_in")
        self.assertEqual(self.call({"op": "session", "token": token})["logged_in"], False)
        self.assertEqual(self.call({"op": "login", "password": PASSWORD})["error"], "wrong_password")
        fresh = self.call({"op": "login", "password": "another horse"})["token"]
        self.assertTrue(self.call({"op": "log", "token": fresh})["ok"])  # stays valid while the file is unchanged
        self.assertTrue(self.call({"op": "log", "token": fresh})["ok"])

    def test_removed_password_file_ends_all_sessions(self):
        token = self.login()
        (self.dir / "private" / "login.json").unlink()
        self.assertEqual(self.call({"op": "log", "token": token})["error"], "not_logged_in")

    def test_runner_cannot_be_dumped(self):
        import subprocess
        code = ("import ctypes,sys; sys.path.insert(0, %r)\n"
                "from pihub import runner\n"
                "libc = ctypes.CDLL(None)\n"
                "before = libc.prctl(3, 0, 0, 0, 0)\n"
                "ok = runner.harden()\n"
                "print(before, ok, libc.prctl(3, 0, 0, 0, 0))" % str(ROOT))
        out = subprocess.run([sys.executable, "-I", "-c", code], capture_output=True, text=True, timeout=30)
        self.assertEqual(out.stdout.split(), ["1", "True", "0"], out.stderr)

    def test_no_password_set(self):
        (self.dir / "private" / "login.json").unlink()
        self.assertEqual(self.call({"op": "status"})["password_set"], False)
        self.assertEqual(self.call({"op": "login", "password": PASSWORD})["error"], "no_password_set")

    def test_lock_after_wrong_tries(self):
        for _ in range(5):
            self.assertEqual(self.call({"op": "login", "password": "wrong password"})["error"], "wrong_password")
        reply = self.call({"op": "login", "password": PASSWORD})  # right, but locked: not even looked at
        self.assertEqual(reply["error"], "locked")
        self.assertTrue(0 < reply["seconds"] <= 60)

    def test_run_echo_and_log_without_output(self):
        token = self.login()
        r = self.call({"op": "run", "token": token, "card": "demo", "action": 0, "peer": "10.0.0.9"})
        self.assertTrue(r["ok"], r)
        self.assertEqual((r["exit"], r["output"], r["label"], r["level"], r["timed_out"]),
                         (0, "hello\n", "Say hello", "read", False))
        self.assertEqual(self.collected, [])  # read: no refresh
        entries = self.call({"op": "log", "token": token})["entries"]
        entry = entries[0]
        self.assertEqual((entry["event"], entry["card"], entry["command"], entry["exit"], entry["peer"]),
                         ("action", "demo", "echo hello", 0, "10.0.0.9"))
        self.assertNotIn("output", entry)
        self.assertNotIn("hello\n", (self.dir / "private" / "actions.log").read_text().replace("echo hello", ""))
        self.assertEqual(stat.S_IMODE(os.stat(self.dir / "private" / "actions.log").st_mode), 0o600)

    def test_change_refreshes_and_survives_a_failing_refresh(self):
        token = self.login()
        r = self.call({"op": "run", "token": token, "card": "demo", "action": 1})
        self.assertEqual((r["ok"], r["refreshed"]), (True, True))
        self.assertEqual(self.collected, [self.dir])

        def boom(_):
            raise RuntimeError("scan broke")
        self.server.runner.collect = boom
        r = self.call({"op": "run", "token": token, "card": "demo", "action": 1})
        self.assertEqual((r["ok"], r["exit"], r["refreshed"]), (True, 0, False))

    def test_refusals_run_nothing_and_are_not_logged(self):
        token = self.login()
        before = len(self.call({"op": "log", "token": token})["entries"])
        cases = [({"action": 2}, "level_copy"), ({"action": 99}, "unknown_action"),
                 ({"card": "ghost", "action": 0}, "unknown_action"),
                 ({"action": 3, "value": "1.2.3.4; touch x"}, "bad_value"),
                 ({"action": 3, "value": "4.4.4.4"}, "bad_value"), ({"action": 0, "value": "x"}, "bad_value")]
        for extra, error in cases:
            r = self.call({"op": "run", "token": token, "card": "demo", **extra})
            self.assertEqual(r["error"], error, extra)
        self.assertEqual(len(self.call({"op": "log", "token": token})["entries"]), before)

    def test_a_command_text_from_outside_is_ignored(self):
        token = self.login()
        marker = self.dir / "pwned"
        r = self.call({"op": "run", "token": token, "card": "demo", "action": 0,
                       "command": f"touch {marker}", "run": f"touch {marker}"})
        self.assertEqual(r["output"], "hello\n")
        self.assertFalse(marker.exists())

    def test_value_is_used_when_on_the_list(self):
        token = self.login()
        r = self.call({"op": "run", "token": token, "card": "demo", "action": 3, "value": "5.6.7.8"})
        self.assertEqual(r["output"], "got 5.6.7.8\n")
        self.assertEqual(self.call({"op": "choices", "token": token, "card": "demo", "action": 3})["values"],
                         ["1.2.3.4", "5.6.7.8"])
        self.assertEqual(self.call({"op": "choices", "token": token, "card": "demo", "action": 0})["values"], [])
        self.assertEqual(self.call({"op": "choices", "token": token, "card": "demo", "action": 2})["error"], "level_copy")

    def test_busy(self):
        token = self.login()
        result = {}

        def slow():
            result["r"] = self.call({"op": "run", "token": token, "card": "demo", "action": 4})
        t = threading.Thread(target=slow)
        t.start()
        time.sleep(0.5)
        self.assertEqual(self.call({"op": "run", "token": token, "card": "demo", "action": 0}),
                         {"ok": False, "error": "busy"})
        t.join()
        self.assertTrue(result["r"]["ok"])
        self.assertTrue(self.call({"op": "run", "token": token, "card": "demo", "action": 0})["ok"])

    def test_timeout_is_reported(self):
        (self.dir / "cards" / "demo.toml").write_text(CARD.replace("timeout = 5", "timeout = 1"))
        r = self.call({"op": "run", "token": self.login(), "card": "demo", "action": 4})
        self.assertEqual((r["ok"], r["timed_out"], r["exit"]), (True, True, None))

    def test_mute_and_unmute(self):
        token = self.login()
        self.assertTrue(self.call({"op": "mute", "token": token, "key": "demo:todo", "reason": "known"})["ok"])
        self.assertIn("demo:todo", mutes.load(self.dir))
        self.assertEqual(self.call({"op": "mute", "token": token, "key": "demo:todo", "reason": "x"})["error"], "bad_reason")
        self.assertEqual(self.call({"op": "mute", "token": token, "key": "", "reason": "known"})["error"], "bad_key")
        self.assertTrue(self.call({"op": "unmute", "token": token, "key": "demo:todo"})["ok"])
        self.assertEqual(mutes.load(self.dir), {})

    def test_handler_exception_does_not_kill_the_runner(self):
        token = self.login()

        def boom(*a, **k):
            raise RuntimeError("bug")
        self.server.runner.op_log = boom
        self.assertEqual(self.call({"op": "log", "token": token}), {"ok": False, "error": "internal"})
        self.assertTrue(self.call({"op": "status"})["ok"])

    def test_other_user_is_turned_away(self):
        self.server.uid = os.geteuid() + 1
        s = socket.socket(socket.AF_UNIX)
        s.connect(self.sock)
        # The runner hangs up without reading. Depending on who is faster that
        # shows as a clean end or as a reset; what matters is: no answer.
        try:
            s.sendall(b'{"op":"status"}\n')
            answer = s.recv(100)
        except (ConnectionResetError, BrokenPipeError):
            answer = b""
        self.assertEqual(answer, b"")
        s.close()
        with self.assertRaises(runner.Unreachable):
            self.call({"op": "status"})

    def test_stale_socket_is_replaced_but_a_live_one_is_not(self):
        with self.assertRaises(OSError):
            runner.make_server(self.dir, self.sock)  # ours is alive
        path = os.path.join(os.path.dirname(self.sock), "stale.sock")
        s = socket.socket(socket.AF_UNIX)
        s.bind(path)
        s.close()  # file stays, nobody listens
        other = runner.make_server(self.dir, path)
        other.server_close()
        plain = os.path.join(os.path.dirname(self.sock), "plain")
        Path(plain).write_text("x")
        with self.assertRaises(OSError):
            runner.make_server(self.dir, plain)
        self.assertEqual(Path(plain).read_text(), "x")


class Web(Base):
    def setUp(self):
        super().setUp()
        self.web = web.make_server(self.dir, "en", "127.0.0.1", 0, [], self.sock)
        threading.Thread(target=self.web.serve_forever, daemon=True).start()
        self.addCleanup(self.web.server_close)
        self.addCleanup(self.web.shutdown)
        self.port = self.web.server_address[1]

    def req(self, method, path, body=None, headers=None, cookie=None, raw=None):
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=30)
        h = dict(headers or {})
        if cookie:
            h["Cookie"] = cookie
        if raw is None and body is not None:
            raw = json.dumps(body).encode()
        conn.request(method, path, raw, h)
        r = conn.getresponse()
        data = r.read()
        conn.close()
        try:
            parsed = json.loads(data)
        except ValueError:
            parsed = data
        return r.status, parsed, r

    def post(self, path, body, cookie=None, **headers):
        h = {"X-PiHub": "1", "Content-Type": "application/json", **headers}
        h = {k: v for k, v in h.items() if v is not None}
        return self.req("POST", path, body, h, cookie)

    def web_login(self):
        status, _, r = self.post("/api/login", {"password": PASSWORD})
        self.assertEqual(status, 200)
        header = r.getheader("Set-Cookie")
        return header, header.split(";")[0]

    def test_session_states(self):
        self.assertEqual(self.req("GET", "/api/session")[1],
                         {"actions": True, "password_set": True, "logged_in": False})
        _, cookie = self.web_login()
        self.assertEqual(self.req("GET", "/api/session", cookie=cookie)[1]["logged_in"], True)

    def test_cookie_attributes(self):
        header, _ = self.web_login()
        self.assertIn("pihub_session=", header)
        for part in ("HttpOnly", "SameSite=Strict", "Path=/", "Max-Age=43200"):
            self.assertIn(part, header)

    def test_login_failures(self):
        status, body, r = self.post("/api/login", {"password": "wrong password"})
        self.assertEqual((status, body["error"]), (401, "wrong_password"))
        self.assertIsNone(r.getheader("Set-Cookie"))
        self.assertEqual(self.post("/api/login", {"password": 5})[0], 400)
        for _ in range(4):
            self.post("/api/login", {"password": "wrong password"})
        status, body, _ = self.post("/api/login", {"password": PASSWORD})
        self.assertEqual((status, body["error"]), (429, "locked"))
        self.assertGreater(body["seconds"], 0)

    def test_without_cookie_401_with_cookie_200(self):
        for method, path, body in (("GET", "/api/log", None), ("GET", "/api/choices?card=demo&action=3", None),
                                   ("POST", "/api/run", {"card": "demo", "action": 0}),
                                   ("POST", "/api/mute", {"key": "k", "reason": "because"}),
                                   ("POST", "/api/unmute", {"key": "k"})):
            if method == "GET":
                status, reply, _ = self.req(method, path)
            else:
                status, reply, _ = self.post(path, body)
            self.assertEqual((status, reply["error"]), (401, "not_logged_in"), path)
        self.assertEqual(self.post("/api/run", {"card": "demo", "action": 0}, cookie="pihub_session=forged")[0], 401)

        _, cookie = self.web_login()
        status, reply, _ = self.post("/api/run", {"card": "demo", "action": 0}, cookie=cookie)
        self.assertEqual((status, reply["output"]), (200, "hello\n"))
        status, reply, _ = self.req("GET", "/api/choices?card=demo&action=3", cookie=cookie)
        self.assertEqual((status, reply["values"]), (200, ["1.2.3.4", "5.6.7.8"]))
        status, reply, _ = self.req("GET", "/api/log?limit=5", cookie=cookie)
        self.assertEqual(status, 200)
        self.assertTrue(all("output" not in e for e in reply["entries"]))
        self.assertEqual(reply["entries"][0]["peer"], "127.0.0.1")
        self.assertEqual(self.post("/api/mute", {"key": "k", "reason": "because"}, cookie=cookie)[0], 200)
        self.assertEqual(self.post("/api/unmute", {"key": "k"}, cookie=cookie)[0], 200)

    def test_error_codes(self):
        _, cookie = self.web_login()
        for body, code, error in (({"card": "demo", "action": 2}, 400, "level_copy"),
                                  ({"card": "demo", "action": 9}, 400, "unknown_action"),
                                  ({"card": "demo", "action": 3, "value": "1.2.3.4; touch x"}, 400, "bad_value")):
            status, reply, _ = self.post("/api/run", body, cookie=cookie)
            self.assertEqual((status, reply["error"]), (code, error))
        self.assertEqual(self.post("/api/mute", {"key": "k", "reason": "x"}, cookie=cookie)[1]["error"], "bad_reason")
        self.assertEqual(self.post("/api/mute", {"key": "", "reason": "fine one"}, cookie=cookie)[1]["error"], "bad_key")
        self.assertEqual(self.req("GET", "/api/choices?card=demo&action=x", cookie=cookie)[0], 400)

    def test_busy_is_409(self):
        _, cookie = self.web_login()
        t = threading.Thread(target=lambda: self.post("/api/run", {"card": "demo", "action": 4}, cookie=cookie))
        t.start()
        time.sleep(0.5)
        status, reply, _ = self.post("/api/run", {"card": "demo", "action": 0}, cookie=cookie)
        t.join()
        self.assertEqual((status, reply["error"]), (409, "busy"))

    def test_logout_clears_the_cookie_and_the_session(self):
        _, cookie = self.web_login()
        status, _, r = self.post("/api/logout", {}, cookie=cookie)
        self.assertEqual(status, 200)
        self.assertIn("Max-Age=0", r.getheader("Set-Cookie"))
        self.assertEqual(self.req("GET", "/api/session", cookie=cookie)[1]["logged_in"], False)

    def test_post_checks_stop_before_the_runner(self):
        _, cookie = self.web_login()
        marker = self.dir / "pwned"
        ok = {"card": "demo", "action": 0}
        runs = lambda: sum(e["event"] == "action" for e in runner.call(self.sock, {  # noqa: E731
            "op": "log", "token": self.login(), "limit": 100})["entries"])
        before = runs()
        attempts = [
            self.post("/api/run", ok, cookie=cookie, **{"X-PiHub": None}),                       # no header
            self.post("/api/run", ok, cookie=cookie, **{"X-PiHub": "0"}),
            self.post("/api/run", ok, cookie=cookie, Origin="http://evil.example"),               # foreign origin
            self.post("/api/run", ok, cookie=cookie, Origin="null"),
            self.post("/api/run", ok, cookie=cookie, Origin="http://127.0.0.1:1"),                # wrong port
            self.post("/api/run", ok, cookie=cookie, **{"Content-Type": "text/plain"}),
            self.post("/api/run", ok, cookie=cookie, **{"Content-Type": None}),
            self.post("/api/run", {"pad": "x" * 11000}, cookie=cookie),                            # too big
            self.req("POST", "/api/run", None, {"X-PiHub": "1", "Content-Type": "application/json"}, cookie, raw=b"[1]"),
            self.req("POST", "/api/run", None, {"X-PiHub": "1", "Content-Type": "application/json"}, cookie, raw=b"{bad"),
            self.req("POST", "/api/run", None, {"X-PiHub": "1", "Content-Type": "application/json"}, cookie, raw=b""),
        ]
        codes = [a[0] for a in attempts]
        self.assertEqual(codes, [403, 403, 403, 403, 403, 400, 400, 400, 400, 400, 400])
        self.assertEqual(runs(), before)
        self.assertFalse(marker.exists())
        # The same origin is fine, also with charset on the content type and any case.
        status, _, _ = self.post("/api/run", ok, cookie=cookie, Origin=f"http://127.0.0.1:{self.port}",
                                 **{"Content-Type": "Application/JSON; charset=utf-8"})
        self.assertEqual(status, 200)

    def test_post_only_forwards_known_fields(self):
        _, cookie = self.web_login()
        marker = self.dir / "pwned"
        status, reply, _ = self.post("/api/run", {"card": "demo", "action": 0, "command": f"touch {marker}"},
                                     cookie=cookie)
        self.assertEqual((status, reply["output"]), (200, "hello\n"))
        self.assertFalse(marker.exists())

    def test_host_name_guard_still_applies(self):
        conn = http.client.HTTPConnection("127.0.0.1", self.port)
        conn.request("POST", "/api/login", json.dumps({"password": PASSWORD}),
                     {"Host": "evil.example", "X-PiHub": "1", "Content-Type": "application/json"})
        self.assertEqual(conn.getresponse().status, 403)
        conn.close()
        conn = http.client.HTTPConnection("127.0.0.1", self.port)
        conn.request("GET", "/api/session", headers={"Host": "evil.example"})
        self.assertEqual(conn.getresponse().status, 403)
        conn.close()

    def test_unknown_post_route_and_security_headers(self):
        status, _, r = self.post("/api/exec", {})
        self.assertEqual(status, 404)
        self.assertEqual(r.getheader("X-Content-Type-Options"), "nosniff")
        _, _, r = self.req("GET", "/api/session")
        self.assertEqual(r.getheader("Cache-Control"), "no-store")
        self.assertIn("default-src 'self'", r.getheader("Content-Security-Policy"))

    def test_runner_off_means_no_actions(self):
        self.server.shutdown()
        self.server.server_close()
        os.unlink(self.sock)
        self.assertEqual(self.req("GET", "/api/session")[1],
                         {"actions": False, "password_set": False, "logged_in": False})
        self.assertEqual(self.post("/api/login", {"password": PASSWORD})[0], 503)
        status, reply, _ = self.post("/api/run", {"card": "demo", "action": 0}, cookie="pihub_session=x")
        self.assertEqual((status, reply["error"]), (503, "runner_unavailable"))


if __name__ == "__main__":
    unittest.main()
