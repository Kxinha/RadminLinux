"""Firewall Exceptions for the native engine: the Linux side of Radmin's
System > Firewall Exceptions menu.

Why: on Windows, Radmin can open the Windows Firewall for apps used over the
VPN. On Linux the OpenRad interface (radminvpn0) falls into the default
firewalld zone ("public"), which answers ping but REJECTS every TCP/UDP
connection a peer opens. Verified on 2026-11-25 against official Radmin VPN on
Windows: the Windows peer could ping us, but could not join a server we host.

What it changes (only when the user picks it in the menu, survives reboot):
  firewalld  Allow All Apps   -> interface in the built-in "trusted" zone
             Add Application  -> interface in our own zone "radmin-vpn"
                                 (default target: ping works, everything else is
                                 refused) plus the ports of the chosen app
  ufw        Allow All Apps   -> ufw allow in on <iface>
             Add Application  -> ufw allow in on <iface> to any port P proto X
firewalld asks for the admin password through polkit (cached a few minutes),
ufw goes through pkexec. OpenRad's own stealth block (Block traffic) still
applies before any of this.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from pathlib import Path

import config
import orctl

ZONE = "radmin-vpn"
STATE = Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config") / "radmin-linux" / "firewall.json"
# never offered in Add Application
SKIP = {"openrad", "radmin-vpn"}


def _run(cmd: list[str], timeout: int = 150) -> tuple[int, str]:
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        return p.returncode, (p.stdout + p.stderr).strip()
    except (OSError, subprocess.TimeoutExpired) as e:
        return 1, str(e)


def iface() -> str:
    d = orctl.call("interface-name")
    name = ((d.get("data") or {}).get("interface_name") if d.get("ok") else "") or "radminvpn0"
    return name if re.fullmatch(r"[A-Za-z0-9_.-]{1,15}", name) else "radminvpn0"


def backend() -> str:
    """'firewalld', 'ufw' or '' (no firewall manager running)."""
    if shutil.which("firewall-cmd") and _run(["firewall-cmd", "--state"], 10)[1] == "running":
        return "firewalld"
    if shutil.which("ufw") and _run(["systemctl", "is-active", "ufw"], 10)[1] == "active":
        try:
            if re.search(r"^ENABLED=yes", Path("/etc/ufw/ufw.conf").read_text(), re.M):
                return "ufw"
        except OSError:
            pass
    return ""


# ------------------------------------------------------------------ state
def _load() -> dict:
    try:
        d = json.loads(STATE.read_text())
        return d if isinstance(d, dict) else {}
    except (OSError, ValueError):
        return {}


def _save(d: dict) -> None:
    STATE.parent.mkdir(parents=True, exist_ok=True)
    STATE.write_text(json.dumps(d, indent=1))


def _fw(*args: str) -> tuple[int, str]:
    return _run(["firewall-cmd", *args])


def status() -> dict:
    """{"backend", "iface", "allow_all": bool, "ports": [[port, proto], ...]}"""
    be = backend()
    st = {"backend": be, "iface": iface(), "allow_all": False, "ports": []}
    if be == "firewalld":
        zone = _fw("--get-zone-of-interface=" + st["iface"])[1].strip()
        if not zone or "no zone" in zone:
            zone = _fw("--permanent", "--get-zone-of-interface=" + st["iface"])[1].strip()
        st["allow_all"] = zone == "trusted"
        if ZONE in _fw("--permanent", "--get-zones")[1].split():
            for p in _fw("--permanent", "--zone=" + ZONE, "--list-ports")[1].split():
                if "/" in p:
                    n, proto = p.split("/", 1)
                    st["ports"].append([n, proto])
    elif be == "ufw":
        s = _load().get(st["iface"], {})
        st["allow_all"] = bool(s.get("allow_all"))
        st["ports"] = [list(p) for p in s.get("ports", [])]
    return st


def _fw_bind(zone: str, name: str) -> tuple[bool, str]:
    """Put the interface in a zone, now and after reboot."""
    out = []
    for perm in ([], ["--permanent"]):
        rc, o = _fw(*perm, "--zone=" + zone, "--change-interface=" + name)
        out.append(o)
        if rc != 0:
            return False, "\n".join(out)
    return True, "\n".join(out)


def _fw_ensure_zone() -> tuple[bool, str]:
    if ZONE in _fw("--get-zones")[1].split():
        return True, ""
    if ZONE not in _fw("--permanent", "--get-zones")[1].split():
        rc, o = _fw("--permanent", "--new-zone=" + ZONE)
        if rc != 0:
            return False, o
        _fw("--permanent", "--zone=" + ZONE, "--set-short=Radmin VPN")
        _fw("--permanent", "--zone=" + ZONE, "--set-description=Radmin-Linux: OpenRad interface. "
            "Ping is answered, connections only to the ports opened in Firewall Exceptions.")
    rc, o = _fw("--reload")   # a new permanent zone only exists at runtime after a reload
    return rc == 0, o


def set_allow_all(on: bool) -> tuple[bool, str]:
    be, name = backend(), iface()
    if be == "firewalld":
        if on:
            return _fw_bind("trusted", name)
        ok, o = _fw_ensure_zone()
        return _fw_bind(ZONE, name) if ok else (False, o)
    if be == "ufw":
        verb = [] if on else ["delete"]
        rc, o = _run(["pkexec", "ufw", *verb, "allow", "in", "on", name])
        if rc == 0:
            d = _load(); d.setdefault(name, {})["allow_all"] = on; _save(d)
        return rc == 0, o
    return False, "No firewall manager (firewalld or ufw) is running, nothing blocks the VPN."


def set_ports(ports: list[tuple[str, str]], on: bool) -> tuple[bool, str]:
    """Open (on) or close the given [port, proto] pairs for VPN peers only."""
    be, name = backend(), iface()
    ports = [(str(int(p)), proto) for p, proto in ports if proto in ("tcp", "udp")]
    if not ports:
        return False, "no port"
    if be == "firewalld":
        ok, o = _fw_ensure_zone()
        if not ok:
            return False, o
        if on and "trusted" != _fw("--get-zone-of-interface=" + name)[1].strip():
            ok, o = _fw_bind(ZONE, name)
            if not ok:
                return False, o
        out = []
        for perm in ([], ["--permanent"]):
            args = [f"--{'add' if on else 'remove'}-port={p}/{proto}" for p, proto in ports]
            rc, o = _fw(*perm, "--zone=" + ZONE, *args)
            out.append(o)
            if rc != 0:
                return False, "\n".join(out)
        return True, "\n".join(out)
    if be == "ufw":
        script = []
        for p, proto in ports:
            script.append(["ufw", *([] if on else ["delete"]), "allow", "in", "on", name,
                           "to", "any", "port", p, "proto", proto])
        # one pkexec prompt for all of them
        cmd = " && ".join(" ".join(c) for c in script)
        rc, o = _run(["pkexec", "/bin/sh", "-c", cmd])
        if rc == 0:
            d = _load(); s = d.setdefault(name, {})
            cur = {tuple(x) for x in s.get("ports", [])}
            cur = (cur | set(ports)) if on else (cur - set(ports))
            s["ports"] = sorted(cur); _save(d)
        return rc == 0, o
    return False, "No firewall manager (firewalld or ufw) is running, nothing blocks the VPN."


# ------------------------------------------------------------------ apps
_USERS = re.compile(r'\("([^"]+)",pid=(\d+)')


def listening_apps() -> list[dict]:
    """Programs of this user that wait for connections a VPN peer could reach:
    [{"name", "pids", "ports": [[port, proto], ...]}], sorted by name.
    Loopback-only sockets are left out (nobody else can reach them anyway)."""
    rc, out = _run(["ss", "-Hlntup"], 10)
    apps: dict[str, dict] = {}
    me = os.getuid()
    for line in out.splitlines():
        f = line.split()
        if len(f) < 6 or f[0] not in ("tcp", "udp"):
            continue
        local = f[4]
        host, _, port = local.rpartition(":")
        host = host.strip("[]").split("%")[0]
        if host.startswith("127.") or host == "::1" or not port.isdigit():
            continue
        if not (host in ("0.0.0.0", "*", "::") or host.startswith("26.")):
            continue
        for name, pid in _USERS.findall(line):
            try:
                if os.stat(f"/proc/{pid}").st_uid != me:
                    continue
                exe = os.path.basename(os.readlink(f"/proc/{pid}/exe"))
            except OSError:
                exe = name
            label = name if name not in ("java", "python3", "python", "wine64-preloader", "wine-preloader") else _cmd_label(pid, name)
            if name in SKIP or exe in SKIP:
                continue
            a = apps.setdefault(label, {"name": label, "pids": set(), "ports": set()})
            a["pids"].add(int(pid)); a["ports"].add((port, f[0]))
    res = []
    for a in apps.values():
        res.append({"name": a["name"], "pids": sorted(a["pids"]),
                    "ports": sorted(a["ports"], key=lambda x: (int(x[0]), x[1]))})
    return sorted(res, key=lambda a: a["name"].lower())


def _cmd_label(pid: str, name: str) -> str:
    """java/python/wine say nothing: use the jar, script or .exe name."""
    try:
        args = Path(f"/proc/{pid}/cmdline").read_bytes().split(b"\0")
    except OSError:
        return name
    for i, a in enumerate(args[1:], 1):
        s = a.decode(errors="replace")
        if s == "-m" and i + 1 < len(args):
            return f"{args[i + 1].decode(errors='replace')} ({name})"
        low = s.lower()
        if low.endswith((".jar", ".py", ".exe")):
            return f"{os.path.basename(s)} ({name})"
    return name
