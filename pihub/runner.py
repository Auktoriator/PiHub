"""The runner: the one trusted part of PiHub that runs actions.

It checks the password, keeps the sessions, reads the cards from disk
itself, runs exactly the command written there and keeps the log. The web
process only passes on requests over a Unix socket and never sends a
command text: only a card, an action number and, where the card asks for
one, a value that is checked here against a fresh measurement.

Protocol: one line of JSON in, one line of JSON out, then the connection
closes. Every answer is {"ok": true, ...} or {"ok": false, "error": key}.
"""
from __future__ import annotations

import ctypes
import json
import os
import socket
import socketserver
import stat
import struct
import sys
import threading
import traceback
from pathlib import Path

from . import actions, auth, mutes, state

DEFAULT_SOCKET = "/run/pihub/runner.sock"
MAX_REQUEST = 64 * 1024
MAX_RESPONSE = 8 * 1024 * 1024
READ_TIMEOUT = 10  # seconds a client may take to send its line
RUN_WAIT = 3600 + 120  # a client must be willing to wait longer than the longest action
PEER_MAX = 100


def harden() -> bool:
    """Make this process undumpable (PR_SET_DUMPABLE = 0). The web page runs
    as the same user; without this it could read the memory of the runner
    (tokens, the password being typed) with ptrace or /proc/<pid>/mem."""
    try:
        libc = ctypes.CDLL(None, use_errno=True)
        if libc.prctl(4, 0, 0, 0, 0) != 0:
            raise OSError(ctypes.get_errno(), "prctl failed")
        return True
    except (OSError, AttributeError) as e:
        print(f"pihub runner: could not make the process undumpable: {e}", file=sys.stderr)
        return False


def fail(error: str, **extra) -> dict:
    return {"ok": False, "error": error, **extra}


class Runner:
    def __init__(self, data_dir: Path, choices: dict | None = None, collect=None,
                 clock=None, mono=None):
        self.data = Path(data_dir)
        self.choices = choices  # None: the real measurements
        self.collect = collect or state.collect
        kw = {"clock": clock} if clock else {}
        self.sessions = auth.Sessions(**kw)
        self.throttle = auth.Throttle(**({"clock": mono} if mono else {}))
        self.login_lock = threading.Lock()
        self.mute_lock = threading.Lock()
        self.run_lock = threading.Lock()  # one action at a time
        self._login_seen = self._login_signature()

    def _login_signature(self):
        try:
            st = os.stat(auth.login_file(self.data))
        except OSError:
            return None
        return (st.st_mtime_ns, st.st_ino)  # a rewrite replaces the file, so the inode changes too

    def _password_changed(self) -> None:
        """A new, removed or replaced password file ends every session."""
        now = self._login_signature()
        if now != self._login_seen:
            self._login_seen = now
            self.sessions.clear()

    # --- entry point: bytes in, bytes out -----------------------------------

    def respond(self, line: bytes) -> bytes:
        try:
            if len(line) > MAX_REQUEST or not line.endswith(b"\n"):
                reply = fail("bad_request")
            else:
                try:
                    request = json.loads(line)
                except ValueError:
                    request = None
                reply = self.handle(request) if isinstance(request, dict) else fail("bad_request")
        except Exception:
            traceback.print_exc(file=sys.stderr)
            reply = fail("internal")
        return (json.dumps(reply, ensure_ascii=False) + "\n").encode()

    def handle(self, request: dict) -> dict:
        op = request.get("op")
        self._password_changed()
        if op in ("status", "login", "logout", "session"):
            return getattr(self, "op_" + op)(request)
        if op in ("choices", "run", "log", "mute", "unmute"):
            if not self.sessions.valid(request.get("token")):
                return fail("not_logged_in")
            return getattr(self, "op_" + op)(request)
        return fail("bad_request")

    # --- without a session --------------------------------------------------

    def op_status(self, req: dict) -> dict:
        return {"ok": True, "password_set": auth.password_is_set(self.data)}

    def op_login(self, req: dict) -> dict:
        password = req.get("password")
        if not isinstance(password, str):
            return fail("bad_request")
        peer = str(req.get("peer") or "?")[:PEER_MAX]
        with self.login_lock:  # one guess at a time: scrypt is slow on purpose
            if not auth.password_is_set(self.data):
                return fail("no_password_set")
            left = self.throttle.remaining()
            if left:
                return fail("locked", seconds=left)  # the password is not even looked at
            if not auth.verify(self.data, password):
                self.throttle.failed()
                actions.log_append(self.data, {"time": actions.now(), "event": "login_failed", "peer": peer})
                return fail("wrong_password")
            self.throttle.succeeded()
            actions.log_append(self.data, {"time": actions.now(), "event": "login", "peer": peer})
            return {"ok": True, "token": self.sessions.login()}

    def op_logout(self, req: dict) -> dict:
        self.sessions.logout(req.get("token"))
        return {"ok": True}

    def op_session(self, req: dict) -> dict:
        return {"ok": True, "logged_in": self.sessions.valid(req.get("token"))}

    # --- with a session -----------------------------------------------------

    def op_choices(self, req: dict) -> dict:
        try:
            values = actions.choices_for(self.data, req.get("card"), req.get("action"), self.choices)
        except actions.ActionError as e:
            return fail(e.key)
        return {"ok": True, "values": values}

    def op_run(self, req: dict) -> dict:
        if not self.run_lock.acquire(blocking=False):
            return fail("busy")
        try:
            try:
                plan = actions.resolve(self.data, req.get("card"), req.get("action"), req.get("value"),
                                       self.choices)
            except actions.ActionError as e:
                return fail(e.key)  # a refused request ran nothing and is not logged
            result = actions.execute(plan["command"], plan["timeout"])
            actions.log_append(self.data, {
                "time": actions.now(), "event": "action", "peer": str(req.get("peer") or "?")[:PEER_MAX],
                "card": req.get("card"), "label": plan["label"], "level": plan["level"],
                "command": plan["command"], "exit": result["exit"], "timed_out": result["timed_out"],
                "seconds": result["seconds"]})  # the output is deliberately not kept
            refreshed = None
            if plan["level"] == "change":
                try:
                    self.collect(self.data)
                    refreshed = True
                except Exception:
                    traceback.print_exc(file=sys.stderr)
                    refreshed = False
            return {"ok": True, **result, "label": plan["label"], "level": plan["level"], "refreshed": refreshed}
        finally:
            self.run_lock.release()

    def op_log(self, req: dict) -> dict:
        limit = req.get("limit", 100)
        if not isinstance(limit, int) or isinstance(limit, bool):
            limit = 100
        return {"ok": True, "entries": actions.log_read(self.data, limit)}

    def op_mute(self, req: dict) -> dict:
        try:
            with self.mute_lock:
                mutes.mute(self.data, req.get("key"), req.get("reason"))
        except mutes.MuteError as e:
            return fail(e.key)
        return {"ok": True}

    def op_unmute(self, req: dict) -> dict:
        try:
            with self.mute_lock:
                mutes.unmute(self.data, req.get("key"))
        except mutes.MuteError as e:
            return fail(e.key)
        return {"ok": True}


# --- socket server ----------------------------------------------------------

class Handler(socketserver.StreamRequestHandler):
    timeout = READ_TIMEOUT

    def handle(self) -> None:
        try:
            creds = self.request.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, struct.calcsize("3i"))
            _pid, uid, _gid = struct.unpack("3i", creds)
            if uid != self.server.uid:
                return  # not the same user: close without a word
            line = self.rfile.readline(MAX_REQUEST + 1)
            self.wfile.write(self.server.runner.respond(line))
        except OSError:
            pass  # client gone, or too slow


class Server(socketserver.ThreadingUnixStreamServer):
    daemon_threads = True
    request_queue_size = 16

    def __init__(self, path: str, runner: Runner, uid: int | None = None):
        self.runner = runner
        self.uid = os.geteuid() if uid is None else uid
        super().__init__(path, Handler)

    def server_bind(self) -> None:
        old = os.umask(0o177)
        try:
            super().server_bind()
        finally:
            os.umask(old)
        os.chmod(self.server_address, 0o600)


def _clear_stale(path: str) -> None:
    """Remove a socket file nobody listens on. Refuse anything else."""
    try:
        mode = os.lstat(path).st_mode
    except FileNotFoundError:
        return
    if not stat.S_ISSOCK(mode):
        raise OSError(f"{path} exists and is not a socket")
    probe = socket.socket(socket.AF_UNIX)
    probe.settimeout(2)
    try:
        probe.connect(path)
    except OSError:
        os.unlink(path)  # orphaned
        return
    finally:
        probe.close()
    raise OSError(f"another runner is already listening on {path}")


def make_server(data_dir: Path, path: str = DEFAULT_SOCKET, **kw) -> Server:
    Path(path).parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    # The private folder must exist before the web service starts: systemd
    # can only hide a path from it that is there at that moment.
    auth.private_dir(data_dir)
    _clear_stale(path)
    return Server(path, Runner(data_dir, **kw))


# --- client (used by the web process) ---------------------------------------

class Unreachable(Exception):
    """The runner does not answer."""


def call(socket_path: str, request: dict, timeout: float = 30.0) -> dict:
    s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    s.settimeout(timeout)
    try:
        s.connect(str(socket_path))
        s.sendall(json.dumps(request).encode() + b"\n")
        data = b""
        while not data.endswith(b"\n") and len(data) < MAX_RESPONSE:
            chunk = s.recv(65536)
            if not chunk:
                break
            data += chunk
        reply = json.loads(data)
    except (OSError, ValueError) as e:
        raise Unreachable(str(e)) from None
    finally:
        s.close()
    if not isinstance(reply, dict):
        raise Unreachable("bad answer")
    return reply
