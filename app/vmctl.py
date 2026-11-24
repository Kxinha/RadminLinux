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
# "stopping" holds the qemu pid while a shutdown is in progress (written by
# power_off and vm-stop.sh), so every app window and the tray can say "Shutting
# down" instead of mistaking the quiet VM for one that is booting.
STOPFLAG = os.path.join(VMDIR, "stopping")
_preflight: "subprocess.Popen | None" = None   # a VM start launched by this app
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
        return pid if b"qemu" in cmd and _alive(pid) else None
    except (OSError, ValueError):
        return None


def _alive(pid: int) -> bool:
    """The process exists and is not a zombie (exited, not yet reaped)."""
    try:
        with open(f"/proc/{pid}/stat") as f:
            return f.read().rsplit(")", 1)[1].split()[0] != "Z"
    except (OSError, IndexError):
        return False


def is_running() -> bool:
    return _pid() is not None


def is_stopping() -> bool:
    """A shutdown was requested and this same qemu is still running."""
    try:
        with open(STOPFLAG) as f:
            want = int(f.read().strip() or 0)
    except (OSError, ValueError):
        return False
    return want != 0 and want == _pid()


def _mark_stopping(pid: int | None):
    try:
        if pid:
            with open(STOPFLAG, "w") as f:
                f.write(str(pid))
        else:
            os.remove(STOPFLAG)
    except OSError:
        pass


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
    pid = _pid()
    if pid is None:
        return True
    _mark_stopping(pid)
    _monitor("system_powerdown")
    for _ in range(timeout):
        if not _alive(pid):
            _mark_stopping(None)
            return True
        time.sleep(1)
    # still going: the flag stays (it only counts for this qemu pid)
    return False


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
            if not _alive(pid):
                break
        else:
            os.kill(pid, signal.SIGKILL)   # truly stuck; absolute last resort
            time.sleep(1)
    except OSError:
        pass
    for f in (PIDFILE, STOPFLAG):
        try:
            os.remove(f)
        except OSError:
            pass
    return True


def power_on() -> bool:
    """Start the VM via preflight (which auto-repairs the stack)."""
    global _preflight
    if is_running():
        return True
    try:
        # own process group, so a Quit can stop it with all its children
        # (qemu itself is started with setsid and is not in that group)
        _preflight = subprocess.Popen(["bash", PREFLIGHT, "-q"], start_new_session=True,
                                      stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return True
    except OSError:
        return False


def starting() -> bool:
    """A VM start launched by this app (preflight.sh) is still running."""
    return _preflight is not None and _preflight.poll() is None


def cancel_start() -> None:
    """Stop a VM start launched by this app (preflight.sh and its children)."""
    if not starting():
        return
    try:
        os.killpg(_preflight.pid, signal.SIGTERM)
        _preflight.wait(5)
    except (OSError, subprocess.TimeoutExpired):
        try:
            os.killpg(_preflight.pid, signal.SIGKILL)
        except OSError:
            pass


def request_power_off(grace: int = 120) -> None:
    """Ask Windows to shut down and return at once, for when the app cannot
    wait (logout). A small detached watcher stops qemu if Windows is still
    not off after `grace` s, so nothing is left behind even then."""
    pid = _pid()
    if pid is None:
        return
    _mark_stopping(pid)
    _monitor("system_powerdown", timeout=2)
    try:
        subprocess.Popen(["sh", "-c",
            'i=0; while [ $i -lt "$2" ] && grep -qa qemu "/proc/$1/cmdline" 2>/dev/null; '
            'do sleep 1; i=$((i+1)); done; '
            'grep -qa qemu "/proc/$1/cmdline" 2>/dev/null && kill "$1"', "sh", str(pid), str(grace)],
            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL, start_new_session=True)
    except OSError:
        pass


def close_viewers() -> None:
    """Close every VNC viewer showing this VM (also ones opened outside the
    app), so none is left with a "connection closed" error once the VM is off."""
    d = os.environ.get("VM_VNC") or config._get("RADMIN_VNC_DISPLAY", "3")
    addr = f"127.0.0.1::{5900 + int(d)}".encode()
    me = os.getuid()
    for e in os.listdir("/proc"):
        if not e.isdigit():
            continue
        try:
            if os.stat(f"/proc/{e}").st_uid != me:
                continue
            with open(f"/proc/{e}/cmdline", "rb") as f:
                argv = f.read().split(b"\0")
        except OSError:
            continue
        if argv and os.path.basename(argv[0]) == b"vncviewer" and addr in argv:
            try:
                os.kill(int(e), signal.SIGTERM)
            except OSError:
                pass


def stop_everything(timeout: int = 120) -> bool:
    """Quit: cancel a VM start in progress, then shut the VM down (ACPI, and
    qemu is stopped if Windows is not off after `timeout` s). True = nothing
    of the VM is left running."""
    if starting():
        cancel_start()
        for _ in range(6):        # it may have launched qemu just before
            if is_running():
                break
            time.sleep(0.5)
    if not is_running():
        return True
    if power_off(timeout):
        return True
    power_off_hard()
    return not is_running()


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
