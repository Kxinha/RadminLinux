"""
orctl.py - the native engine: OpenRad (open source Radmin VPN client) instead of
the Windows VM.

Same names as vmctl.py (is_running, power_on, power_off, ...) so the window and
the tray drive either engine the same way, plus the OpenRad actions (join,
create, leave, rename, diagnostics). Everything goes through the `openrad` CLI
with --json; the CLI talks to the per-user OpenRad service over its private
Unix socket, so each call takes a few ms.

OpenRad: https://github.com/gabrielmf1998/openrad (copy of
github.com/gringoestrangeiro/openrad, MIT). Not affiliated with Famatech.
"""
from __future__ import annotations
import json, os, shutil, subprocess, tempfile, time
from pathlib import Path

import config

VIA = {"DirectUdp": "UDP", "DirectTcp": "TCP", "Relay": "TcpRelay"}


def binary() -> str:
    return config.OPENRAD_BIN


def installed() -> bool:
    return os.access(binary(), os.X_OK)


def _base() -> list[str]:
    cmd = [binary(), "--language", "en"]
    if config.OPENRAD_DATA:
        cmd += ["--data-dir", config.OPENRAD_DATA]
    return cmd


def call(*args: str, timeout: float = 20) -> dict:
    """Run one openrad command. Always returns a dict with at least "ok"."""
    if not installed():
        return {"ok": False, "message": f"OpenRad not found at {binary()}"}
    import backend   # tracked, killable subprocess (shutdown frees the threads)
    rc, out = backend.run_capture(_base() + list(args) + ["--json"], int(timeout))
    if rc == 124:
        return {"ok": False, "message": "OpenRad did not answer in time"}
    if rc == 255 and not out:
        return {"ok": False, "message": "cancelled"}
    # the JSON reply is the last JSON line; anything else is a log line
    for line in reversed(out.strip().splitlines()):
        line = line.strip()
        if line.startswith("{"):
            try:
                d = json.loads(line)
                return d if isinstance(d, dict) else {"ok": False, "message": line}
            except ValueError:
                break
    return {"ok": rc == 0, "message": out.strip()[-300:]}


_BUSY = ("reconnecting", "retry shortly", "not connected", "connecting")


def act(*args: str, timeout: float = 60, wait: float = 30) -> dict:
    """An action that needs a connected service. Right after start, a rename or
    a network change the service reconnects for a few seconds and refuses
    actions with "retry shortly"; wait for it instead of failing."""
    end = time.monotonic() + wait
    while True:
        d = call(*args, timeout=timeout)
        msg = str(d.get("message") or "").lower()
        if d.get("ok") or not any(w in msg for w in _BUSY) or time.monotonic() > end:
            return d
        time.sleep(1.5)


def _ok(d: dict) -> tuple[bool, str]:
    return bool(d.get("ok")), str(d.get("message") or "")


# ---------------------------------------------------------------- identity
def has_identity() -> bool:
    return (data_dir() / "profile" / "identity.json").exists()


def identity() -> dict:
    """{"name", "ip"} of this profile, read from identity.json (only these two
    fields; the credential in it is never touched). {} before the first start."""
    try:
        d = json.loads((data_dir() / "profile" / "identity.json").read_text())
        return {"name": str(d.get("node_name") or ""), "ip": str(d.get("vip") or "")}
    except (OSError, ValueError, AttributeError):
        return {}


def data_dir() -> Path:
    if config.OPENRAD_DATA:
        return Path(config.OPENRAD_DATA)
    state = os.environ.get("XDG_STATE_HOME") or str(Path.home() / ".local/state")
    return Path(state) / "openrad"


def init(node_name: str) -> tuple[bool, str]:
    """Register a new Radmin identity (new RID and 26.x address)."""
    return _ok(call("init", "--node-name", node_name, timeout=60))


def reset_identity(node_name: str) -> tuple[bool, str]:
    """Stop, keep the old profile aside (it holds the old identity), register anew."""
    call("stop", timeout=30)
    _wait_stopped(30)
    d = data_dir()
    if d.exists():
        d.rename(d.with_name(f"{d.name}.old-{time.strftime('%Y%m%d-%H%M%S')}"))
    ok, msg = init(node_name)
    if ok:
        ok, msg = _ok(call("start", timeout=60))
    return ok, msg


# ---------------------------------------------------------------- power (vmctl API)
def status() -> dict:
    d = call("status", timeout=8)
    data = d.get("data") if isinstance(d.get("data"), dict) else {}
    return data


def is_running() -> bool:
    """The OpenRad service is up (connecting or connected)."""
    if not installed():
        return False
    return status().get("phase") not in (None, "stopped")


def is_stopping() -> bool:
    return False


def starting() -> bool:
    return False


def power_on() -> bool:
    if not has_identity():
        ok, _ = init(config.OPENRAD_NODE_NAME or os.uname().nodename)
        if not ok:
            return False
    return bool(call("start", timeout=60).get("ok"))


def _wait_stopped(timeout: float) -> bool:
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if not is_running():
            return True
        time.sleep(0.5)
    return False


def power_off(timeout: int = 30) -> bool:
    call("stop", timeout=timeout)
    return _wait_stopped(timeout)


def power_off_hard() -> bool:
    pid = status().get("process_id")
    if isinstance(pid, int) and pid > 1:
        try:
            os.kill(pid, 9)
        except OSError:
            pass
    return _wait_stopped(5)


def stop_everything(timeout: int = 30) -> bool:
    return power_off(timeout) or power_off_hard()


def request_power_off(grace: int = 30) -> None:
    """Logout: ask the service to stop without waiting."""
    if not installed():
        return
    try:
        subprocess.Popen(_base() + ["stop"], stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                         stderr=subprocess.DEVNULL, start_new_session=True)
    except OSError:
        pass


def cancel_start() -> None:
    pass


def close_viewers() -> None:
    pass


def is_responsive(timeout: float = 3) -> bool:
    return bool(status().get("phase"))


def recover() -> str:
    return "ok"


def retry_interface() -> tuple[bool, str]:
    return _ok(call("retry-interface", timeout=150))


# ---------------------------------------------------------------- state
def fetch_state():
    """backend.State for the window, in the same shape as the VM agent."""
    import backend
    if not installed():
        return backend.State(ok=False, vm_running=False,
                             error="OpenRad is not installed (run the installer again)")
    data = status()
    phase = data.get("phase")
    if phase in (None, "stopped"):
        err = data.get("error") or ("no Radmin identity yet, turn on to create one"
                                    if not has_identity() else "OpenRad is off")
        return backend.State(ok=False, vm_running=False, error=str(err))
    snap = data.get("snapshot") if isinstance(data.get("snapshot"), dict) else None
    if phase != "connected" or not snap:
        return backend.State(ok=False, vm_running=True,
                             error=str(data.get("error") or "connecting to the Radmin server"))
    peers = snap.get("peers") if isinstance(snap.get("peers"), dict) else {}
    nets = []
    for n in snap.get("networks") or []:
        nid = n.get("network_id")
        members = []
        for rid, role in ((snap.get("roles") or {}).get(nid) or {}).items():
            p = peers.get(str(rid))
            if not p:
                continue
            info = p.get("peer") or {}
            vip = str(info.get("vip") or "")
            if not vip or vip == snap.get("vip"):
                continue
            members.append({"name": str(info.get("name") or ""), "ip": vip,
                            "online": p.get("status") == "Connected",
                            "via": VIA.get(str(p.get("transport") or ""), ""),
                            "rid": int(info.get("rid") or 0), "role": role})
        nets.append({"name": str(n.get("name") or "?"), "id": nid, "members": members,
                     "role": ((snap.get("roles") or {}).get(nid) or {}).get(str(data.get("rid")))})
    iface_err = snap.get("interface_error")
    prefs = data.get("preferences") if isinstance(data.get("preferences"), dict) else {}
    radmin = {"ok": True, "status": "online", "alias": data.get("node_name") or "",
              "ip": snap.get("vip") or "", "networks": nets,
              "blocked": {str(k): str(v).lower() for k, v in (prefs.get("blocked") or {}).items()},
              "force_relay": bool(prefs.get("force_relay"))}
    st = backend.State(ok=True, vm_running=True, node_ip=str(snap.get("vip") or ""),
                       alias=str(data.get("node_name") or ""), service="Running",
                       networks=[n["id"] for n in nets], ts=int(time.time()),
                       api="openrad", radmin=radmin)
    if data.get("interface_disabled"):
        st.error = "started without a network interface"
    elif not snap.get("interface_ready"):
        st.error = f"network interface not ready{': ' + str(iface_err) if iface_err else ''}"
    return st


def networks() -> list[dict]:
    """[{"id", "name", "role"}] of the joined networks."""
    d = call("networks", timeout=15)
    return [n for n in (d.get("data") or []) if isinstance(n, dict)]


# ---------------------------------------------------------------- actions
def _with_password(password: str, fn):
    """Pass a password through a private file that is deleted right after."""
    if not password:
        return fn(None)
    fd, path = tempfile.mkstemp(prefix="radmin-pw-", dir=os.environ.get("XDG_RUNTIME_DIR"))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(password)
        return fn(path)
    finally:
        try:
            os.remove(path)
        except OSError:
            pass


def _wait_list(name: str, present: bool, timeout: float = 20) -> None:
    """join/leave reply first and update the network list a moment later."""
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if any(n.get("name") == name for n in networks()) == present:
            return
        time.sleep(1)


def join(name: str, password: str = "") -> tuple[bool, str]:
    def go(pw):
        args = ["join", name] + (["--password-file", pw] if pw else [])
        return _ok(act(*args))
    ok, msg = _with_password(password, go)
    if ok:
        _wait_list(name, True)
    return ok, msg


def create(name: str, password: str) -> tuple[bool, str]:
    ok, msg = _with_password(password, lambda pw: _ok(act("create", name, "--password-file", pw)))
    if ok:
        _wait_list(name, True)
    return ok, msg


def leave(network: str) -> tuple[bool, str]:
    ok, msg = _ok(act("leave", network))
    if ok:
        _wait_list(network, False)
    return ok, msg


def rename(new_name: str) -> tuple[bool, str]:
    return _ok(act("rename", new_name, timeout=30))


def search(query: str = "") -> list[dict]:
    d = act("search", query, timeout=30)
    data = d.get("data") if isinstance(d.get("data"), dict) else {}
    return [n for n in data.get("networks") or [] if isinstance(n, dict)]


def ping(rid: int) -> tuple[bool, str]:
    return _ok(act("ping", str(rid), timeout=20))


# ---------------------------------------------------------------- admin (role 2 only)
# Roles in OpenRad: 0 = pending approval, 1 = member, 2 = admin. The service
# refuses these for non admins; the app only offers them to admins anyway.
ROLE_NAMES = {0: "pending approval", 1: "member", 2: "admin"}


def delete(network_id: str) -> tuple[bool, str]:
    """Delete a network for every member (admin only, cannot be undone)."""
    ok, msg = _ok(act("delete", "--yes", network_id))
    if ok:
        end = time.monotonic() + 20
        while time.monotonic() < end and any(n.get("id") == network_id for n in networks()):
            time.sleep(1)
    return ok, msg


def kick(network_id: str, rid: int) -> tuple[bool, str]:
    return _ok(act("kick", network_id, str(rid)))


def grant_admin(network_id: str, rid: int) -> tuple[bool, str]:
    return _ok(act("grant-admin", network_id, str(rid)))


def revoke_admin(network_id: str, rid: int) -> tuple[bool, str]:
    return _ok(act("revoke-admin", network_id, str(rid)))


# ---------------------------------------------------------------- peers and preferences
def retry_peers() -> tuple[bool, str]:
    return _ok(act("retry-peers", timeout=30))


def preferences() -> dict:
    """Service preferences (force_relay, blocked, ...), {} when off."""
    p = (call("status", timeout=8).get("data") or {}).get("preferences")
    return p if isinstance(p, dict) else {}


def blocked() -> dict:
    """{ip: "both" | "incoming" | "outgoing"} of the stealth blocked addresses."""
    d = call("blocked", timeout=10)
    b = (d.get("data") or {}).get("blocked") if isinstance(d.get("data"), dict) else None
    return {str(k): str(v).lower() for k, v in (b or {}).items()}


def block(ip: str, direction: str = "both") -> tuple[bool, str]:
    """Drop the traffic of one 26.x address; the peer stays connected and is
    not told. direction: both, incoming (ignore what it sends) or outgoing."""
    extra = {"incoming": ["--incoming"], "outgoing": ["--outgoing"]}.get(direction, [])
    return _ok(call("block", ip, *extra, timeout=10))


def unblock(ip: str) -> tuple[bool, str]:
    return _ok(call("unblock", ip, timeout=10))


def force_relay(enabled: bool) -> tuple[bool, str]:
    """Relays only (no direct UDP/TCP). Saved in the profile, applies at once."""
    return _ok(call("force-relay", "true" if enabled else "false", timeout=30))


def diagnostics() -> dict:
    """Checks for the System > Diagnostics dialog."""
    if not installed():
        return {"error": f"OpenRad not found at {binary()}"}
    d = call("diagnostics", timeout=20)
    cur = ((d.get("data") or {}).get("current") or {}) if isinstance(d.get("data"), dict) else {}
    checks = [
        {"name": "openrad_service", "ok": cur.get("phase") == "connected"},
        {"name": "radmin_server", "ok": isinstance(cur.get("latency_ms"), int)},
        {"name": "interface", "ok": bool(cur.get("interface_ready"))},
        {"name": "helper_rule", "ok": Path(config.OPENRAD_POLKIT_COPY).exists()},
    ]
    return {"checks": checks, "all_ok": all(c["ok"] for c in checks), "current": cur,
            "version": version()}


def version() -> str:
    if not installed():
        return ""
    try:
        out = subprocess.run([binary(), "--version"], capture_output=True, text=True, timeout=5).stdout
        return out.split()[-1] if out.strip() else ""
    except (OSError, subprocess.TimeoutExpired):
        return ""


if __name__ == "__main__":
    s = fetch_state()
    print("ok:", s.ok, "| running:", s.vm_running, "| error:", s.error or "-")
    print("node:", s.alias, s.node_ip)
    for n in (s.radmin or {}).get("networks", []):
        on = sum(1 for m in n["members"] if m["online"])
        print(f"  {n['name']}: {on}/{len(n['members'])} connected")
