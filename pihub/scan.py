"""Read-only inventory of this machine.

The scan collects facts: names, states, ports, mounts. It changes nothing.

What it deliberately does not do, so that no credential can end up in the
inventory:
  * no `docker inspect` (environment variables hold passwords)
  * no process command lines, only process names
  * no file contents, except a fixed set of nginx directives and the
    mount point, type and harmless flags of /etc/fstab

Every section stands alone. A section that cannot be read (tool missing,
sudo asks for a password) is recorded with its reason instead of data.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import socket
import subprocess
import time
from pathlib import Path

SCHEMA = 1
UNIT_TYPES = ("service", "timer", "path", "socket", "target")
SEARCH_PATH = os.environ.get("PATH", "") + ":/usr/sbin:/sbin"
NGINX_KEEP = re.compile(
    r"^\s*(listen|server_name|proxy_pass|allow|deny|auth_request|ssl_verify_client|return)\b"
)
FSTAB_FLAGS = {"noauto", "nofail", "ro", "rw", "defaults"}


# Why a section is missing. The page words each one differently:
MISSING_TOOL = "missing_tool"  # the program is not installed
NEEDS_SUDO = "needs_sudo"      # it is there, but only root may use it and sudo asks for a password
FAILED = "failed"              # anything else; the message says what
SUDO_REFUSED = ("password is required", "a terminal is required", "not in the sudoers",
                "may not run sudo", "is not allowed to run sudo", "need to be root", "must be root",
                "permission denied", "operation not permitted")


class Unavailable(Exception):
    """A section could not be read. The message says why; `why` says what kind
    of reason it is and `tool` which program it concerns."""

    def __init__(self, message: str, why: str = FAILED, tool: str = ""):
        super().__init__(message)
        self.why, self.tool = why, tool


def run(cmd: list[str], sudo: bool = False, timeout: int = 30) -> str:
    tool = shutil.which(cmd[0], path=SEARCH_PATH)
    if tool is None:
        raise Unavailable(f"{cmd[0]}: not installed", MISSING_TOOL, cmd[0])
    full = [tool, *cmd[1:]]
    if sudo and os.geteuid() != 0:
        if shutil.which("sudo", path=SEARCH_PATH) is None:
            raise Unavailable(f"{cmd[0]}: needs root, and sudo is not installed", NEEDS_SUDO, cmd[0])
        full = ["sudo", "-n", *full]
    try:
        p = subprocess.run(full, capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        raise Unavailable(f"{cmd[0]}: no answer after {timeout} s", FAILED, cmd[0]) from None
    if p.returncode != 0:
        lines = (p.stderr or p.stdout).strip().splitlines()
        message = lines[-1] if lines else f"{cmd[0]}: exit {p.returncode}"
        refused = sudo and any(s in (p.stderr or p.stdout).lower() for s in SUDO_REFUSED)
        raise Unavailable(message, NEEDS_SUDO if refused else FAILED, cmd[0])
    return p.stdout


def read(path: str) -> str:
    try:
        return Path(path).read_text(errors="replace")
    except OSError:
        return ""


# --- sections ---------------------------------------------------------------

def host() -> dict:
    os_name = ""
    for line in read("/etc/os-release").splitlines():
        if line.startswith("PRETTY_NAME="):
            os_name = line.split("=", 1)[1].strip('"')
    mem_kb = 0
    for line in read("/proc/meminfo").splitlines():
        if line.startswith("MemTotal:"):
            mem_kb = int(line.split()[1])
    u = os.uname()
    return {
        "name": socket.gethostname(),
        "model": read("/proc/device-tree/model").rstrip("\x00\n"),
        "os": os_name,
        "kernel": u.release,
        "arch": u.machine,
        "memory_mb": mem_kb // 1024,
    }


def units() -> dict:
    kinds = ",".join(UNIT_TYPES)
    loaded = json.loads(run(["systemctl", "list-units", f"--type={kinds}", "--all",
                             "--no-pager", "--output=json"]))
    files = json.loads(run(["systemctl", "list-unit-files", f"--type={kinds}",
                            "--no-pager", "--output=json"]))
    enabled = {f["unit_file"]: f["state"] for f in files}

    # "own" = a real file in /etc/systemd/system: written by the owner or by
    # an installer, not shipped by a package and not a mere enable-link.
    own: set[str] = set()
    etc = Path("/etc/systemd/system")
    if etc.is_dir():
        own = {p.name for p in etc.iterdir()
               if p.is_file() and not p.is_symlink() and p.suffix.lstrip(".") in UNIT_TYPES}

    out: dict[str, dict] = {}
    for u in loaded:
        if u.get("load") == "not-found":
            continue
        out[u["unit"]] = {
            "active": u["active"], "sub": u["sub"],
            "enabled": enabled.get(u["unit"], ""),
            "description": u.get("description", ""),
            "own": u["unit"] in own,
        }
    for name in own - out.keys():
        out[name] = {"active": "inactive", "sub": "dead", "enabled": enabled.get(name, ""),
                     "description": "", "own": True}

    owned = sorted(n for n in out if out[n]["own"])
    if owned:
        text = run(["systemctl", "show", "--no-pager", "-p", "Id", "-p", "WantedBy",
                    "-p", "PartOf", "-p", "Requires", *owned])
        for block in text.strip().split("\n\n"):
            props = dict(line.split("=", 1) for line in block.splitlines() if "=" in line)
            if props.get("Id") in out:
                out[props["Id"]].update(
                    wanted_by=props.get("WantedBy", "").split(),
                    part_of=props.get("PartOf", "").split(),
                    requires=props.get("Requires", "").split(),
                )
    return out


def timers() -> list:
    rows = json.loads(run(["systemctl", "list-timers", "--all", "--no-pager", "--output=json"]))

    def seconds(value):
        return value // 1_000_000 if isinstance(value, int) and value > 0 else None

    return [{"unit": r.get("unit"), "activates": r.get("activates"),
             "next": seconds(r.get("next")), "last": seconds(r.get("last"))} for r in rows]


def containers() -> list:
    cmd = ["docker", "ps", "-a", "--format", "{{json .}}"]
    try:
        text = run(cmd)
    except Unavailable as first:
        if first.why == MISSING_TOOL:
            raise
        # Not in the docker group: the socket may still be open to sudo.
        try:
            text = run(cmd, sudo=True)
        except Unavailable as second:
            raise (second if second.why == NEEDS_SUDO else first) from None
    out = []
    for line in text.splitlines():
        if not line.strip():
            continue
        c = json.loads(line)
        out.append({"name": c.get("Names"), "image": c.get("Image"), "state": c.get("State"),
                    "status": c.get("Status"), "ports": c.get("Ports")})
    return out


def _listening(flag: str, proto: str, sudo: bool) -> list:
    out = []
    for line in run(["ss", "-H", flag, "-l", "-n", "-p"], sudo=sudo).splitlines():
        fields = line.split()
        if len(fields) < 5 or ":" not in fields[3]:
            continue
        addr, port = fields[3].rsplit(":", 1)
        if not port.isdigit():
            continue
        # Java and others report IPv4 loopback as [::ffff:127.0.0.1].
        bare = addr.split("%")[0].strip("[]").removeprefix("::ffff:")
        if bare.startswith("127.") or bare == "::1":
            bound = "local"
        elif bare in ("0.0.0.0", "*", "::"):
            bound = "all"
        else:
            bound = "address"
        out.append({"proto": proto, "port": int(port), "address": addr, "bound": bound,
                    "processes": sorted(set(re.findall(r'\("([^"]+)",pid=', line)))})
    return out


def ports() -> dict:
    # Without root, ss cannot name the processes of other users.
    try:
        rows, named = _listening("-t", "tcp", True) + _listening("-u", "udp", True), True
    except Unavailable:
        rows, named = _listening("-t", "tcp", False) + _listening("-u", "udp", False), False
    seen, unique = set(), []
    for r in rows:
        key = (r["proto"], r["port"], r["address"])
        if key not in seen:
            seen.add(key)
            unique.append(r)
    unique.sort(key=lambda r: (r["port"], r["proto"], r["address"]))
    return {"process_names": named, "listening": unique}


def firewall() -> dict:
    text = run(["ufw", "status", "verbose"], sudo=True)
    out = {"tool": "ufw", "status": "", "default": "", "rules": []}
    in_rules = False
    for line in text.splitlines():
        if line.startswith("Status:"):
            out["status"] = line.split(":", 1)[1].strip()
        elif line.startswith("Default:"):
            out["default"] = line.split(":", 1)[1].strip()
        elif line.startswith("--"):
            in_rules = True
        elif in_rules and line.strip():
            rule, _, comment = line.partition(" # ")
            parts = re.split(r"\s{2,}", rule.strip())
            if len(parts) >= 3:
                out["rules"].append({"to": parts[0], "action": parts[1], "from": parts[2],
                                     "comment": comment.strip()})
    return out


def blocks() -> dict:
    """Addresses currently locked out, and why (CrowdSec)."""
    data = json.loads(run(["cscli", "decisions", "list", "-o", "json"], sudo=True) or "null") or []
    out = []
    for alert in data:
        country = (alert.get("source") or {}).get("cn", "")
        # One alert can carry several entries; only those with a decision count.
        for d in alert.get("decisions") or []:
            out.append({"value": d.get("value"), "scope": d.get("scope"), "type": d.get("type"),
                        "reason": d.get("scenario"), "remaining": d.get("duration"),
                        "origin": d.get("origin"), "country": country})
    return {"tool": "crowdsec", "decisions": out}


def _lsblk() -> dict:
    try:
        return json.loads(run(["lsblk", "-J", "-o", "NAME,SIZE,TYPE,FSTYPE,LABEL,MOUNTPOINTS,MODEL,TRAN,RM"]))
    except Unavailable as e:
        if e.why == MISSING_TOOL:
            raise
    # util-linux before 2.37 knows only one MOUNTPOINT per device.
    tree = json.loads(run(["lsblk", "-J", "-o", "NAME,SIZE,TYPE,FSTYPE,LABEL,MOUNTPOINT,MODEL,TRAN,RM"]))

    def fix(node):
        node["mountpoints"] = [node.pop("mountpoint", None)]
        for child in node.get("children") or []:
            fix(child)

    for dev in tree.get("blockdevices", []):
        fix(dev)
    return tree


def storage() -> dict:
    tree = _lsblk()
    devices = []

    def walk(node, parent=None):
        mounts = [m for m in (node.get("mountpoints") or []) if m]
        devices.append({"name": node.get("name"), "parent": parent, "size": node.get("size"),
                        "type": node.get("type"), "fstype": node.get("fstype"),
                        "label": node.get("label"), "model": (node.get("model") or "").strip(),
                        "transport": node.get("tran"), "removable": bool(node.get("rm")),
                        "mounted_at": mounts})
        for child in node.get("children") or []:
            walk(child, node.get("name"))

    for dev in tree.get("blockdevices", []):
        if dev.get("type") not in ("loop", "ram"):
            walk(dev)

    usage = []
    for d in devices:
        for target in d["mounted_at"]:
            if target.startswith("["):
                continue
            try:
                s = os.statvfs(target)
            except OSError:
                continue
            total = s.f_blocks * s.f_frsize
            usage.append({"target": target, "device": d["name"], "total_bytes": total,
                          "free_bytes": s.f_bavail * s.f_frsize,
                          "used_percent": round(100 * (1 - s.f_bavail / s.f_blocks)) if s.f_blocks else 0})

    planned = []  # what /etc/fstab says should be mounted (options may hold credentials: flags only)
    for line in read("/etc/fstab").splitlines():
        parts = line.split()
        if len(parts) < 4 or parts[0].startswith("#"):
            continue
        if parts[2] in ("swap", "proc", "sysfs", "tmpfs", "devpts"):
            continue
        by = parts[0].split("=", 1)[0] if "=" in parts[0] else "path"
        planned.append({"target": parts[1], "fstype": parts[2], "identified_by": by,
                        "flags": sorted(set(parts[3].split(",")) & FSTAB_FLAGS)})
    mounted = {t for d in devices for t in d["mounted_at"]}
    for p in planned:
        p["mounted"] = p["target"] in mounted
    return {"devices": devices, "usage": usage, "planned": planned}


def web() -> dict:
    base = Path("/etc/nginx/sites-enabled")
    if not base.is_dir():
        raise Unavailable("nginx: no sites-enabled folder", MISSING_TOOL, "nginx")
    sites = []
    for path in sorted(base.iterdir()):
        try:
            lines = path.read_text(errors="replace").splitlines()
        except OSError as e:
            sites.append({"name": path.name, "error": e.strerror})
            continue
        keep = [re.sub(r"\s+", " ", ln.split("#", 1)[0].strip()) for ln in lines if NGINX_KEEP.match(ln)]
        sites.append({"name": path.name, "directives": sorted(set(keep))})
    return {"tool": "nginx", "sites": sites}


def home() -> dict:
    base = Path.home()
    entries = []
    for p in sorted(base.iterdir(), key=lambda p: p.name.lower()):
        if p.name.startswith("."):
            continue
        kind = "link" if p.is_symlink() else "dir" if p.is_dir() else "file"
        is_git = False
        if kind == "dir":
            try:
                is_git = (p / ".git").exists()
            except OSError:
                pass
        entries.append({"name": p.name, "kind": kind, "git": is_git})
    return {"path": str(base), "entries": entries}


def vitals() -> dict:
    """A few readings straight from /sys and /proc. A missing value is None."""
    def text(path):
        try:
            return Path(path).read_text()
        except OSError:
            return ""

    out = {"temp_c": None, "load1": None, "mem_used_percent": None, "uptime_s": None}
    try:
        out["temp_c"] = round(int(text("/sys/class/thermal/thermal_zone0/temp").strip()) / 1000, 1)
    except ValueError:
        pass
    try:
        out["load1"] = float(text("/proc/loadavg").split()[0])
    except (ValueError, IndexError):
        pass
    try:
        mem = {}
        for line in text("/proc/meminfo").splitlines():
            k, _, v = line.partition(":")
            mem[k] = int(v.split()[0])
        out["mem_used_percent"] = round(100 * (1 - mem["MemAvailable"] / mem["MemTotal"]))
    except (ValueError, IndexError, KeyError, ZeroDivisionError):
        pass
    try:
        out["uptime_s"] = int(float(text("/proc/uptime").split()[0]))
    except (ValueError, IndexError):
        pass
    return out


SECTIONS = {
    "host": host, "units": units, "timers": timers, "containers": containers,
    "ports": ports, "firewall": firewall, "blocks": blocks, "storage": storage,
    "web": web, "home": home, "vitals": vitals,
}


def collect() -> dict:
    inv = {"schema": SCHEMA, "taken_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"), "sections": {}}
    for name, fn in SECTIONS.items():
        try:
            inv["sections"][name] = {"ok": True, "data": fn()}
        except Unavailable as e:
            inv["sections"][name] = {"ok": False, "error": str(e), "why": e.why, "tool": e.tool}
        except (OSError, RuntimeError, ValueError, KeyError, TypeError) as e:
            inv["sections"][name] = {"ok": False, "error": f"{type(e).__name__}: {e}", "why": FAILED, "tool": ""}
    return inv


def write(inv: dict, path: Path) -> None:
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        json.dump(inv, f, indent=1, ensure_ascii=False)
        f.write("\n")
    os.replace(tmp, path)


def load(path: Path) -> dict:
    return json.loads(path.read_text())


def section(inv: dict, name: str, default):
    s = inv.get("sections", {}).get(name, {})
    return s["data"] if s.get("ok") else default


def unreadable(inv: dict, name: str) -> bool:
    """True if the scan tried this section and could not read it. A section
    that is not in the inventory at all is not 'unreadable': it was not tried."""
    s = inv.get("sections", {}).get(name)
    return s is not None and not s.get("ok")
