"""
config.py - central configuration for Radmin-Linux (Python side).
Priority: environment variables > ~/.config/radmin-linux/config.env > defaults.
The installer writes config.env.
"""
from __future__ import annotations
import os
from pathlib import Path

VERSION = "0.3.0"
CONFIG_ENV = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) / "radmin-linux" / "config.env"
CODE_ROOT = Path(__file__).resolve().parent.parent      # dir with env.sh, agent/, shim/


def _load_env_file() -> dict[str, str]:
    vals: dict[str, str] = {}
    try:
        for line in CONFIG_ENV.read_text().splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            vals[k.strip()] = v.strip().strip('"').strip("'")
    except OSError:
        pass
    return vals


_FILE = _load_env_file()


def _get(key: str, default: str) -> str:
    return os.environ.get(key) or _FILE.get(key) or default


HOME = _get("RADMIN_HOME", str(Path.home() / ".local/share/radmin-linux"))
VMDIR = _get("RADMIN_VMDIR", f"{HOME}/vm")
VENV = _get("RADMIN_VENV", f"{HOME}/venv")

# network / VM access
HOST = _get("RADMIN_HOST", "192.168.137.1")
CRED = _get("RADMIN_CRED", "bench:bench")
TARGET = _get("RADMIN_TARGET", f"{CRED}@{HOST}")
API_PORT = int(_get("RADMIN_API_PORT", "47950"))
API_URL = f"http://{HOST}:{API_PORT}"

# isolated host<->VM link
TAP = _get("RADMIN_TAP", "tapradmin")
NMCON = _get("RADMIN_NMCON", "radmin-bridge")
ISO_MAC = _get("RADMIN_ISO_MAC", "52:54:00:26:00:02")

# derived paths
PYTHON = f"{VENV}/bin/python"
WMIEXEC = f"{PYTHON} {VENV}/bin/wmiexec.py"
RUN_SCRIPT = _get("RADMIN_RUN", f"{VMDIR}/bench-run.sh")
VIEW_SCRIPT = _get("RADMIN_VIEW", f"{VMDIR}/bench-view.sh")

# paths in the VM (Windows)
SHIM_PATH = _get("RADMIN_SHIM", r"C:\radmin-shim.ps1")
AGENT_DIR = _get("RADMIN_AGENT_DIR", r"C:\radmin-agent")
AGENT_SRC = str(CODE_ROOT / "agent")       # agent scripts shipped with this version

# VM footprint, declared in the UI
VM_RAM = _get("RADMIN_VM_RAM", "1024")   # MB
VM_SMP = _get("RADMIN_VM_SMP", "1")      # vCPUs


def agent_version() -> str:
    """Version of the agent scripts shipped with this app (agent/VERSION)."""
    try:
        return (Path(AGENT_SRC) / "VERSION").read_text().strip()
    except OSError:
        return ""


def vm_footprint() -> str:
    """Human label for the header/About, e.g. '1 GB · 1 CPU'."""
    try:
        ram = int(VM_RAM)
        r = f"{ram // 1024} GB" if ram >= 1024 and ram % 1024 == 0 else f"{ram} MB"
    except ValueError:
        r = f"{VM_RAM} MB"
    return f"{r} · {VM_SMP} CPU"


if __name__ == "__main__":
    for k in ("VERSION", "HOME", "VMDIR", "VENV", "HOST", "API_URL", "TARGET", "TAP",
              "NMCON", "WMIEXEC", "RUN_SCRIPT", "AGENT_SRC"):
        print(f"{k:12} = {globals()[k]}")
    print(f"{'AGENT_VER':12} = {agent_version()}")
