"""The web page: one HTML page, its script and style, and JSON.

It runs no commands and writes nothing. Login and action requests are only
passed on to the runner over a Unix socket; the runner checks the password,
reads the cards itself and runs what is written there. This process never
sends a command text, and it keeps no session of its own.
"""
from __future__ import annotations

import ipaddress
import json
import socket
from http.cookies import CookieError, SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from . import cards as cardlib
from . import history, mutes, runner, scan, state, views
from .texts import PAGE, TEXTS

PAGE_DIR = Path(__file__).resolve().parent / "page"
FILES = {
    "/": ("index.html", "text/html; charset=utf-8"),
    "/app.js": ("app.js", "text/javascript; charset=utf-8"),
    "/app.css": ("app.css", "text/css; charset=utf-8"),
}
HEADERS = {
    "Content-Security-Policy": "default-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'none'",
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "no-referrer",
    "Cache-Control": "no-store",
}
COOKIE = "pihub_session"
COOKIE_ATTRS = "HttpOnly; SameSite=Strict; Path=/"
POST_MAX = 10 * 1024
STATUS = {"not_logged_in": 401, "busy": 409, "locked": 429, "internal": 500, "bad_request": 400,
          "bad_value": 400, "unknown_action": 400, "level_copy": 400, "bad_reason": 400, "bad_key": 400,
          "wrong_password": 401, "no_password_set": 401}
JSON_TYPE = "application/json; charset=utf-8"


def payload(data: Path, lang: str) -> dict:
    areas, cards, problems = cardlib.load(data)
    try:
        inv = scan.load(data / "inventory.json")
    except (OSError, ValueError):
        inv = {"sections": {}}
    lang = lang if lang in TEXTS else "en"
    current = state.load(data)
    security = views.security(inv, cards)
    for row in security["ports"]:
        # "only on this machine" and "not listening" are already findings from the collect run.
        if row["verdict"] == "differs" and row["bound"] != "local" and row["card"] in current["cards"]:
            current["findings"].append({"card": row["card"], "kind": "port_differs",
                                        "key": f"{row['card']}:port_differs:{row['port']}",
                                        "port": f"{row['port']}/{row['proto']}",
                                        "meant": row["meant"], "effective": row["effective"]})
    # Silenced findings are marked, and every card's status is worked out again from what still counts.
    current = views.apply_mutes(current, mutes.load(data))
    return {
        "lang": lang,
        # Whether a scan exists, and for each section whether it could be read and why not.
        # The page says so in words wherever a missing section would look like "nothing to report".
        "inventory": bool(inv.get("sections")),
        "sections": {name: {"ok": bool(s.get("ok")), "why": s.get("why", "failed"), "tool": s.get("tool", ""),
                            "error": str(s.get("error", ""))}
                     for name, s in inv.get("sections", {}).items() if isinstance(s, dict)},
        "process_names": bool(scan.section(inv, "ports", {}).get("process_names", True)),
        "texts": {**TEXTS["en"], **PAGE["en"], **TEXTS[lang], **PAGE[lang]},
        "host": scan.section(inv, "host", {}),
        "scanned_at": inv.get("taken_at", ""),
        "areas": areas,
        "cards": cards,
        "format_problems": len(problems),
        "state": current,
        "security": security,
        "storage": scan.section(inv, "storage", {"devices": [], "usage": [], "planned": []}),
        "files": views.files(inv, cards),
        "history": history.view(data),
        "vitals": scan.section(inv, "vitals", {}),
    }


class Handler(BaseHTTPRequestHandler):
    server_version = "PiHub"

    def host_allowed(self) -> bool:
        """Refuse names this machine does not go by (DNS rebinding)."""
        host = (self.headers.get("Host") or "").strip()
        if host.startswith("["):
            host = host[1:].split("]")[0]
        else:
            host = host.rsplit(":", 1)[0] if ":" in host else host
        try:
            ipaddress.ip_address(host)
            return True
        except ValueError:
            return host.lower() in self.server.names

    def send(self, status: int, body: bytes, content_type: str, extra=()) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        for key, value in HEADERS.items():
            self.send_header(key, value)
        for key, value in extra:
            self.send_header(key, value)
        self.end_headers()
        self.wfile.write(body)

    def send_json(self, status: int, obj: dict, extra=()) -> None:
        self.send(status, json.dumps(obj, ensure_ascii=False).encode(), JSON_TYPE, extra)

    # --- the runner ---------------------------------------------------------

    def token(self) -> str | None:
        try:
            jar = SimpleCookie(self.headers.get("Cookie") or "")
        except CookieError:
            return None
        morsel = jar.get(COOKIE)
        return morsel.value if morsel else None

    def ask(self, request: dict, timeout: float = 60.0) -> dict | None:
        """The runner's answer, or None when it cannot be reached."""
        try:
            return runner.call(self.server.runner_socket, request, timeout)
        except runner.Unreachable:
            return None

    def forward(self, request: dict, timeout: float = 60.0) -> None:
        """Pass a request to the runner and turn its answer into an HTTP reply."""
        token = self.token()
        if not token:
            self.send_json(401, {"ok": False, "error": "not_logged_in"})
            return
        reply = self.ask({**request, "token": token}, timeout)
        if reply is None:
            self.send_json(503, {"ok": False, "error": "runner_unavailable"})
            return
        status = 200 if reply.get("ok") else STATUS.get(reply.get("error"), 500)
        extra = [("Set-Cookie", f"{COOKIE}=; {COOKIE_ATTRS}; Max-Age=0")] if status == 401 else []
        self.send_json(status, reply, extra)

    def api_get(self, path: str, query: dict) -> bool:
        """The GET routes of the runner. False: not one of them."""
        if path == "/api/session":
            status = self.ask({"op": "status"}, 3)
            if status is None or not status.get("ok"):
                self.send_json(200, {"actions": False, "password_set": False, "logged_in": False})
                return True
            token = self.token()
            session = self.ask({"op": "session", "token": token}, 3) if token else None
            self.send_json(200, {"actions": True, "password_set": bool(status.get("password_set")),
                                 "logged_in": bool(session and session.get("logged_in"))})
        elif path == "/api/choices":
            try:
                action = int(query.get("action", [""])[0])
            except ValueError:
                self.send_json(400, {"ok": False, "error": "bad_request"})
                return True
            self.forward({"op": "choices", "card": query.get("card", [""])[0], "action": action})
        elif path == "/api/log":
            try:
                limit = max(1, min(int(query.get("limit", ["100"])[0]), 500))
            except ValueError:
                limit = 100
            self.forward({"op": "log", "limit": limit})
        else:
            return False
        return True

    # --- POST ---------------------------------------------------------------

    def post_body(self) -> dict | None:
        """The checks every POST must pass. Sends the refusal itself and
        returns None when one fails; nothing then reaches the runner."""
        if self.headers.get("X-PiHub") != "1":
            self.send_json(403, {"ok": False, "error": "bad_request"})
            return None
        origin = self.headers.get("Origin")
        if origin is not None and urlsplit(origin).netloc.lower() != (self.headers.get("Host") or "").strip().lower():
            self.send_json(403, {"ok": False, "error": "bad_request"})
            return None
        ctype = (self.headers.get("Content-Type") or "").split(";")[0].strip().lower()
        length = self.headers.get("Content-Length") or ""
        if (ctype != "application/json" or self.headers.get("Transfer-Encoding")
                or not length.isdigit() or int(length) > POST_MAX):
            self.send_json(400, {"ok": False, "error": "bad_request"})
            return None
        try:
            body = json.loads(self.rfile.read(int(length)))
        except ValueError:
            body = None
        if not isinstance(body, dict):
            self.send_json(400, {"ok": False, "error": "bad_request"})
            return None
        return body

    def do_POST(self) -> None:
        if not self.host_allowed():
            self.send(403, b"unknown host name\n", "text/plain; charset=utf-8")
            return
        path = self.path.split("?", 1)[0]
        if path not in ("/api/login", "/api/logout", "/api/run", "/api/mute", "/api/unmute"):
            self.send(404, b"not found\n", "text/plain; charset=utf-8")
            return
        body = self.post_body()
        if body is None:
            return
        peer = self.client_address[0]
        if path == "/api/login":
            password = body.get("password")
            if not isinstance(password, str):
                self.send_json(400, {"ok": False, "error": "bad_request"})
                return
            reply = self.ask({"op": "login", "password": password, "peer": peer})
            if reply is None:
                self.send_json(503, {"ok": False, "error": "runner_unavailable"})
            elif reply.get("ok") and isinstance(reply.get("token"), str):
                self.send_json(200, {"ok": True},
                               [("Set-Cookie", f"{COOKIE}={reply['token']}; {COOKIE_ATTRS}; Max-Age=43200")])
            else:
                error = reply.get("error")
                extra = [("Retry-After", str(reply["seconds"]))] if error == "locked" and "seconds" in reply else []
                out = {"ok": False, "error": error}
                if "seconds" in reply:
                    out["seconds"] = reply["seconds"]
                self.send_json(STATUS.get(error, 500), out, extra)
        elif path == "/api/logout":
            token = self.token()
            if token:
                self.ask({"op": "logout", "token": token}, 5)
            self.send_json(200, {"ok": True}, [("Set-Cookie", f"{COOKIE}=; {COOKIE_ATTRS}; Max-Age=0")])
        elif path == "/api/run":
            self.forward({"op": "run", "card": body.get("card"), "action": body.get("action"),
                          "value": body.get("value"), "peer": peer}, runner.RUN_WAIT)
        elif path == "/api/mute":
            self.forward({"op": "mute", "key": body.get("key"), "reason": body.get("reason")})
        else:
            self.forward({"op": "unmute", "key": body.get("key")})

    def do_GET(self) -> None:
        if not self.host_allowed():
            self.send(403, b"unknown host name\n", "text/plain; charset=utf-8")
            return
        path, _, query = self.path.partition("?")
        if path in FILES:
            name, content_type = FILES[path]
            self.send(200, (PAGE_DIR / name).read_bytes(), content_type)
        elif path == "/api/data":
            body = json.dumps(payload(self.server.data, self.server.lang), ensure_ascii=False).encode()
            self.send(200, body, "application/json; charset=utf-8")
        elif not self.api_get(path, parse_qs(query)):
            self.send(404, b"not found\n", "text/plain; charset=utf-8")


def make_server(data: Path, lang: str, host: str, port: int, names: list[str],
                runner_socket: str = runner.DEFAULT_SOCKET) -> ThreadingHTTPServer:
    server = ThreadingHTTPServer((host, port), Handler)
    server.daemon_threads = True
    server.data, server.lang, server.runner_socket = data, lang, runner_socket
    own = socket.gethostname().lower()
    server.names = {"localhost", own, own + ".local", *(n.lower() for n in names)}
    return server


def serve(data: Path, lang: str, host: str, port: int, names: list[str],
          runner_socket: str = runner.DEFAULT_SOCKET) -> None:
    server = make_server(data, lang, host, port, names, runner_socket)
    print(f"PiHub: http://{host}:{port}/", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
