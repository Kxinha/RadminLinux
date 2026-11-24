"""
vmctl.py - controls the whole VM from the Linux side (not Radmin, the machine).
For the user, "turn Radmin Linux on/off" = turn the VM on/off.
Uses the QEMU monitor (socket), the pidfile and preflight.sh.
"""
from __future__ import annotations
import os, socket, subprocess, time, signal

import config
VMDIR = config.VMDIR
MONITOR = os.path.join(VMDIR, "monitor.sock")
PIDFILE = os.path.join(VMDIR, "qemu.pid")
RUN = config.RUN_SCRIPT
PREFLIGHT = os.path.join(os.path.dirname(os.path.dirname(__file__)), "preflight.sh")


def _pid() -> int | None:
    """PID of OUR qemu (pidfile checked against /proc, so a stale pidfile whose
    number was reused by another program does not count)."""
    try:
        with open(PIDFILE) as f:
            pid = int(f.read().strip())
        with open(f"/proc/{pid}/cmdline", "rb") as f:
            cmd = f.read()
        return pid if b"qemu" in cmd else None
    except (OSError, ValueError):
        return None


def is_running() -> bool:
    return _pid() is not None


def _monitor(cmd: str, timeout: float = 8) -> str:
    """Send a command to the QEMU monitor and return the reply."""
    if not os.path.exists(MONITOR):
        return ""
    try:
        s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        s.settimeout(timeout)
        s.connect(MONITOR)
        time.sleep(0.2)
        s.recv(4096)  # banner
        s.sendall((cmd + "\n").encode())
        time.sleep(0.4)
        data = s.recv(8192)
        s.close()
        return data.decode(errors="replace")
    except OSError:
        return ""


def power_off(timeout: int = 60) -> bool:
    """Shut the VM down (ACPI). Wait for it to actually exit."""
    if not is_running():
        return True
    _monitor("system_powerdown")
    pid = _pid()
    if pid is None:
        return True
    for _ in range(timeout):
        try:
            os.kill(pid, 0)
        except OSError:
            return True
        time.sleep(1)
    return False  # did not shut down in time


def power_off_hard() -> bool:
    """Last resort: stop the QEMU process. Uses SIGTERM (qemu shuts down in an
    orderly way and flushes its block cache), escalating to SIGKILL only if it
    refuses to exit. NEVER start with kill -9: it dumps GBs of dirty pages at once
    and freezes the host."""
    pid = _pid()
    if pid is None:
        return True
    try:
        os.kill(pid, signal.SIGTERM)
        for _ in range(30):            # up to ~15s for an orderly exit
            time.sleep(0.5)
            try:
                os.kill(pid, 0)
            except OSError:
                break
        else:
            os.kill(pid, signal.SIGKILL)   # truly stuck; absolute last resort
            time.sleep(1)
    except OSError:
        pass
    try:
        os.remove(PIDFILE)
    except OSError:
        pass
    return True


def power_on() -> bool:
    """Start the VM via preflight (which auto-repairs the stack)."""
    if is_running():
        return True
    try:
        subprocess.Popen(["bash", PREFLIGHT, "-q"],
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return True
    except OSError:
        return False


def is_responsive(timeout: float = 3) -> bool:
    """Does the VM answer? Agent API first, then ICMP (process-free socket), so
    a VM whose agent is still starting is not taken for a hung one."""
    import backend
    if backend.api_version(timeout=timeout) is not None:
        return True
    try:
        import actions
        return bool(actions.ping_sweep([config.HOST]).get(config.HOST, False))
    except Exception:  # noqa
        return False


def recover() -> str:
    """Watchdog: if the process is alive but the VM does not answer (hung),
    force a power off and back on. Returns what it did."""
    if not is_running():
        power_on()
        return "started"
    if is_responsive():
        return "ok"
    # process alive but no reply = hung
    if not power_off(timeout=30):
        power_off_hard()
    power_on()
    return "recovered"


if __name__ == "__main__":
    import sys
    if len(sys.argv) > 1:
        a = sys.argv[1]
        if a == "status":
            print("running" if is_running() else "stopped")
        elif a == "off":
            print("off ok" if power_off() else "off failed")
        elif a == "on":
            print("on ok" if power_on() else "on failed")
    else:
        print("usage: vmctl.py status|on|off")
