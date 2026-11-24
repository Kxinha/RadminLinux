"""
backend.py - talks to the Windows VM and returns the Radmin state.

Two transports:
  * the agent API (api.ps1 in the VM, HTTP on the isolated link): fast, < 1 s.
  * WMI through impacket's wmiexec (the old way, ~10-20 s): only a fallback for
    VMs whose agent predates the API, and to install/upgrade the agent itself.
"""
from __future__ import annotations
import http.client, json, re, subprocess, threading
from dataclasses import dataclass, field
from urllib.parse import urlencode

import config
VENV = config.VENV
WMIEXEC = config.WMIEXEC
TARGET = config.TARGET
SHIM_PATH = config.SHIM_PATH

_MARK_RE = re.compile(r"<<<RADMINJSON>>>\s*(.*?)\s*<<<END>>>", re.S)


@dataclass
class Peer:
    ip: str
    mac: str = ""
    type: str = "dynamic"
    host: str = ""          # NetBIOS hostname
    name: str = ""          # local nickname
    online: bool = True


@dataclass
class State:
    ok: bool = False
    vm_running: bool = False
    node_ip: str = ""
    hostname: str = ""
    alias: str = ""
    service: str = "Unknown"
    peers: list[Peer] = field(default_factory=list)
    networks: list[str] = field(default_factory=list)
    ts: int = 0
    error: str = ""
    api: str = ""              # agent API version ("" = answered over WMI)
    gui: bool = False          # Radmin GUI process running in the VM
    identity_reset: str = ""   # set once after the agent gave this VM a new identity


_last: "State | None" = None

# Live subprocesses and HTTP connections, so shutdown can kill/close them and
# free the worker threads (otherwise Qt aborts with "QThread destroyed while running").
_LIVE_PROCS: set = set()
_LIVE_CONNS: set = set()
_CANCEL = threading.Event()


# ---------------------------------------------------------------- agent API
def api(path: str, method: str = "GET", params: dict | None = None,
        timeout: float = 8) -> dict | None:
    """Call the in-VM agent API. Returns the JSON dict, or None when the API is
    not reachable (VM off/booting, or an old agent without the API)."""
    if _CANCEL.is_set():
        return None
    url = path + ("?" + urlencode(params) if params else "")
    conn = http.client.HTTPConnection(config.HOST, config.API_PORT, timeout=timeout)
    _LIVE_CONNS.add(conn)
    try:
        conn.request(method, url, body=b"" if method == "POST" else None,
                     headers={"Connection": "close"})
        r = conn.getresponse()
        data = r.read()
        if r.status != 200:
            return None
        return json.loads(data.decode("utf-8", "replace"))
    except (OSError, ValueError, http.client.HTTPException):
        return None
    finally:
        _LIVE_CONNS.discard(conn)
        conn.close()


def api_version(timeout: float = 3) -> str | None:
    """Agent version if the API answers, else None."""
    d = api("/ping", timeout=timeout)
    return str(d.get("version", "")) if isinstance(d, dict) else None


# ---------------------------------------------------------------- subprocesses
def run_capture(cmd, timeout: int, shell: bool = False) -> tuple[int, str]:
    """Tracked and KILLABLE Popen. Returns (rc, output). rc 124=timeout, 255=cancelled."""
    if _CANCEL.is_set():
        return 255, ""
    try:
        p = subprocess.Popen(cmd, shell=shell, stdout=subprocess.PIPE,
                             stderr=subprocess.PIPE, stdin=subprocess.DEVNULL, text=True)
    except OSError as e:
        return 255, str(e)
    _LIVE_PROCS.add(p)
    try:
        out, err = p.communicate(timeout=timeout)
        return (p.returncode if p.returncode is not None else 255), (out or "") + (err or "")
    except subprocess.TimeoutExpired:
        p.kill()
        try:
            out, err = p.communicate(timeout=5)
        except Exception:  # noqa
            out, err = "", ""
        return 124, (out or "") + (err or "")
    finally:
        _LIVE_PROCS.discard(p)


def cancel_all() -> None:
    """Shutdown: no new work, kill live subprocesses, close live HTTP sockets."""
    _CANCEL.set()
    for p in list(_LIVE_PROCS):
        try:
            p.kill()
        except Exception:  # noqa
            pass
    for c in list(_LIVE_CONNS):
        try:
            if c.sock:
                c.sock.shutdown(2)
            c.close()
        except Exception:  # noqa
            pass


def wmi_ps(script: str, timeout: int = 90) -> tuple[int, str]:
    """Run a PowerShell block in the VM over WMI (EncodedCommand, stdin closed:
    PowerShell 2.0 otherwise waits on stdin forever)."""
    import base64
    b64 = base64.b64encode(script.encode("utf-16-le")).decode()
    cmd = (f"{WMIEXEC} {TARGET} "
           f'"powershell -NoProfile -NonInteractive -InputFormat None -ExecutionPolicy Bypass '
           f'-EncodedCommand {b64} < NUL"')
    return run_capture(cmd, timeout, shell=True)


def wmi_cmd(command: str, timeout: int = 90) -> tuple[int, str]:
    """Run a cmd.exe command line in the VM over WMI."""
    return run_capture(f'{WMIEXEC} {TARGET} "{command}"', timeout, shell=True)


# ---------------------------------------------------------------- state
def _state_from_dict(d: dict, api_ver: str = "") -> State:
    st = State(
        ok=bool(d.get("ok")),
        vm_running=True,
        node_ip=d.get("node_ip") or "",
        hostname=d.get("hostname") or "",
        alias=d.get("alias") or "",
        service=d.get("service") or "Unknown",
        networks=list(d.get("networks", [])),
        ts=int(d.get("ts") or 0),
        api=api_ver,
        gui=bool(d.get("gui", False)),
        identity_reset=d.get("identity_reset") or "",
    )
    for p in d.get("peers", []):
        st.peers.append(Peer(ip=p.get("ip", ""), mac=p.get("mac", ""),
                             type=p.get("type", "dynamic"), host=p.get("host", "")))
    return st


def _fetch_wmi(timeout: int) -> State:
    rc, raw = wmi_cmd(f"powershell -NoProfile -NonInteractive -InputFormat None "
                      f"-ExecutionPolicy Bypass -File {SHIM_PATH} < NUL", timeout)
    if rc == 124:
        return State(ok=False, vm_running=True, error="timed out talking to the VM")
    m = _MARK_RE.search(raw)
    if not m:
        if "rpc_s_access_denied" in raw:
            return State(ok=False, vm_running=True, error="access denied (UAC/credentials)")
        return State(ok=False, vm_running=True, error="VM starting (waiting for Windows)")
    blob = re.sub(r"[\r\n]+", "", m.group(1))     # WMI wraps long lines
    try:
        d = json.loads(blob)
    except json.JSONDecodeError:
        try:
            d = json.loads(re.sub(r"\s+", "", m.group(1)))
        except json.JSONDecodeError as e:
            return State(ok=False, vm_running=True, error=f"invalid JSON: {e}")
    return _state_from_dict(d)


def fetch_state(timeout: int = 40, allow_wmi: bool = True) -> State:
    """State of Radmin in the VM. API first; WMI only when allow_wmi and the API
    is missing (old agent)."""
    global _last
    try:
        import vmctl
        if not vmctl.is_running():
            return State(ok=False, vm_running=False, error="VM is off")
    except Exception:  # noqa
        pass
    d = api("/state", timeout=10)
    if isinstance(d, dict) and d.get("ok"):
        _last = _state_from_dict(d, str(d.get("api", "")) or "?")
        return _last
    if not allow_wmi:
        return State(ok=False, vm_running=True, error="VM starting (waiting for Windows)")
    st = _fetch_wmi(timeout)
    if st.ok:
        _last = st
    return st


def list_networks() -> list[str]:
    """GUIDs of the joined networks, from the last state."""
    return list(_last.networks) if _last else []


if __name__ == "__main__":
    s = fetch_state()
    print("ok:", s.ok, "| api:", s.api or "-", "| error:", s.error or "-")
    print("node:", s.alias, s.node_ip, "| service:", s.service, "| networks:", len(s.networks))
    for p in s.peers:
        print(f"  peer {p.ip:16} {p.mac} {p.host}")
