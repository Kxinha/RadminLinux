"""
agent.py - the in-VM agent (C:\\radmin-agent): health, updates, network
orchestration, and installing/upgrading the agent itself.

Everything goes through the agent API when it is up; WMI is the fallback for
old agents and the only way to install a new one.
"""
from __future__ import annotations
import io, json, os, re
import backend
import config

AGENT_DIR = config.AGENT_DIR
_UPD_RE = re.compile(r"<<<UPD>>>\s*(.*?)\s*<<<END>>>", re.S)
_HEALTH_RE = re.compile(r"<<<HEALTH>>>\s*(.*?)\s*<<<END>>>", re.S)
SHIM_SRC = os.path.join(str(config.CODE_ROOT), "shim", "radmin-shim.ps1")


def _run_file(ps_path: str, args: str = "", timeout: int = 90) -> str:
    rc, blob = backend.wmi_cmd(
        "powershell -NoProfile -NonInteractive -InputFormat None -ExecutionPolicy Bypass "
        f"-File {ps_path} {args} < NUL", timeout)
    if rc == 124:
        return "timeout"
    if rc == 255:
        return blob or "cancelled"
    return blob


def _json_between(rx: re.Pattern, blob: str) -> dict | None:
    m = rx.search(blob)
    if not m:
        return None
    try:
        return json.loads(re.sub(r"[\r\n]+", "", m.group(1)))
    except json.JSONDecodeError:
        try:
            return json.loads(re.sub(r"\s+", "", m.group(1)))
        except json.JSONDecodeError:
            return None


def orchestrate_net(timeout: int = 120) -> tuple[bool, str]:
    d = backend.api("/orchestrate", "POST", timeout=timeout)
    if d is not None:
        return bool(d.get("ok")), str(d.get("msg", ""))
    out = _run_file(rf"{AGENT_DIR}\net-orchestrator.ps1", timeout=timeout)
    ok = ("connectivity ready" in out) or ("conectividade pronta" in out)
    return ok, out


def power_guard(timeout: int = 60) -> tuple[bool, str]:
    out = _run_file(rf"{AGENT_DIR}\power-guard.ps1", timeout=timeout)
    return ("=== OK ===" in out), out


def check_update(install: bool = False, timeout: int = 600) -> dict:
    d = backend.api("/update", params={"install": "1"} if install else None, timeout=timeout)
    if d is not None:
        return d
    out = _run_file(rf"{AGENT_DIR}\radmin-update.ps1", "" if install else "-CheckOnly", timeout)
    return _json_between(_UPD_RE, out) or {"error": "no answer from the updater"}


def health(heal: bool = False, timeout: int = 180) -> dict:
    """Full VM diagnosis (and self repair when heal=True)."""
    d = backend.api("/health", params={"heal": "1"} if heal else None, timeout=timeout)
    if d is not None:
        return d
    out = _run_file(rf"{AGENT_DIR}\health.ps1", "-Heal" if heal else "", timeout)
    return _json_between(_HEALTH_RE, out) or {"error": "no health report", "all_ok": False, "checks": []}


# ---------------------------------------------------------------- install / upgrade
def _smb():
    from impacket.smbconnection import SMBConnection
    user, pw = config.CRED.split(":", 1)
    c = SMBConnection(config.HOST, config.HOST, timeout=60)
    c.login(user, pw)
    return c


def push_files(log=print) -> bool:
    """Copy agent/* to C:\\radmin-agent and the shim to C:\\radmin-shim.ps1 (SMB)."""
    try:
        c = _smb()
    except Exception as e:  # noqa
        log(f"SMB login failed: {e}")
        return False
    try:
        try:
            c.createDirectory("C$", "radmin-agent")
        except Exception:  # noqa
            pass
        for name in sorted(os.listdir(config.AGENT_SRC)):
            src = os.path.join(config.AGENT_SRC, name)
            if not os.path.isfile(src) or not (name.endswith((".ps1", ".vbs")) or name == "VERSION"):
                continue
            data = open(src, "rb").read()
            c.putFile("C$", "\\radmin-agent\\" + name, io.BytesIO(data).read)
            log(f"sent {name} ({len(data)} bytes)")
        if os.path.isfile(SHIM_SRC):
            data = open(SHIM_SRC, "rb").read()
            c.putFile("C$", "\\radmin-shim.ps1", io.BytesIO(data).read)
            log("sent radmin-shim.ps1")
        return True
    except Exception as e:  # noqa
        log(f"copy failed: {e}")
        return False
    finally:
        try:
            c.close()
        except Exception:  # noqa
            pass


def install(log=print) -> bool:
    """Push the agent and (re)register its scheduled tasks, then start the API."""
    if not push_files(log):
        return False
    out = _run_file(rf"{AGENT_DIR}\agent-install.ps1", timeout=180)
    if "<<<AGENTOK>>>" not in out:
        log("agent-install.ps1 did not finish:\n" + out[-800:])
        return False
    log("scheduled tasks registered")
    # the boot task (identity check, hardening, ICS) normally runs at power-on
    backend.wmi_cmd('schtasks /run /tn "RadminAgent-Boot"', 60)
    import time
    for _ in range(30):
        if backend.api_version(timeout=2) == config.agent_version():
            log("agent API is up")
            return True
        time.sleep(1)
    log("agent installed, but the API is not answering yet")
    return True


def ensure_agent(force: bool = False, log=print) -> str:
    """Make sure the VM runs the agent version shipped with this app.
    Returns 'ok', 'upgraded' or 'failed'."""
    import fcntl
    want = config.agent_version()
    have = backend.api_version(timeout=3)
    if have == want and not force:
        return "ok"
    # one installer at a time (the app and preflight.sh may both try after a boot)
    os.makedirs(config.VMDIR, exist_ok=True)
    with open(os.path.join(config.VMDIR, "agent-install.lock"), "w") as lk:
        fcntl.flock(lk, fcntl.LOCK_EX)
        have = backend.api_version(timeout=3)
        if have == want and not force:
            return "ok"
        log(f"agent in the VM: {have or 'none/old'}; installing {want}")
        return "upgraded" if install(log) else "failed"


def agent_installed() -> bool:
    return backend.api_version(timeout=3) is not None


if __name__ == "__main__":
    import sys
    a = sys.argv[1] if len(sys.argv) > 1 else "check"
    if a == "ensure":
        r = ensure_agent(force="--force" in sys.argv)
        print("agent:", r)
        sys.exit(0 if r != "failed" else 1)
    elif a == "deploy":
        sys.exit(0 if install() else 1)
    elif a == "net":
        print("net ok:", orchestrate_net()[0])
    elif a == "health":
        print(json.dumps(health(heal="--heal" in sys.argv), indent=2))
    elif a == "version":
        print("shipped:", config.agent_version(), "| in VM:", backend.api_version())
    else:
        print(json.dumps(check_update(install=False), indent=2))
