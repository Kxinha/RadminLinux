"""
actions.py - write actions (phase 3/4) and active liveness.
- connect/disconnect: starts/stops RvControlSvc (the mesh's real power switch).
- rename_node, leave_network, reset_identity, show_gui.
- ping_sweep: measures online/offline straight from Linux (we have a route to 26.0.0.0/8).
Every action goes to the in-VM agent API first (instant) and falls back to WMI
for old agents.
"""
from __future__ import annotations
import concurrent.futures, ipaddress, re
import socket, struct, select, time, threading
import backend  # API client + WMI fallback + the killable-subprocess registry
import config

REGBASE = r"HKLM:\SOFTWARE\Wow6432Node\Famatech\RadminVPN\1.0"

# Liveness (ping sweep) safety limits. The sweep runs every 30s and must NEVER
# turn into a storm of processes that freezes the user's machine:
#  - Primary path is PROCESS-FREE: unprivileged ICMP sockets (no `ping` process at
#    all), so a storm is physically impossible -- there are zero ping processes.
#  - MAX_PING: hard cap on targets per sweep, whatever the roster holds.
#  - Fallback (only where ICMP sockets are denied) uses `ping -n` (no reverse DNS:
#    26.x has no PTR and would hang the resolver) bounded by a GLOBAL semaphore so
#    at most PING_WORKERS ping processes can ever exist system-wide.
MAX_PING = 300
PING_WORKERS = 16
_ICMP_ECHO = 8


def _run_ps(script: str, timeout: int = 60) -> tuple[bool, str]:
    """WMI fallback: run a PowerShell block on the VM (old agents without the API)."""
    rc, blob = backend.wmi_ps(script, timeout)
    if rc == 124:
        return False, "timeout"
    if rc == 255:
        return False, blob or "cancelled"
    return ("<<<ACTOK>>>" in blob), blob


def _api_act(path: str, params: dict | None = None, timeout: float = 45) -> tuple[bool, str] | None:
    """POST an action to the agent API. None = API not reachable (use WMI)."""
    d = backend.api(path, "POST", params, timeout=timeout)
    if d is None:
        return None
    return bool(d.get("ok")), str(d.get("msg", ""))


def connect(timeout: int = 60) -> tuple[bool, str]:
    r = _api_act("/connect", timeout=timeout)
    if r is not None:
        return r
    ps = (
        f'Set-ItemProperty "{REGBASE}" -Name PowerOn -Value 1 -Type DWord;'
        'Start-Service RvControlSvc;'
        'Start-Sleep 3;'
        'if((Get-Service RvControlSvc).Status -eq "Running"){Write-Output "<<<ACTOK>>>"}'
    )
    return _run_ps(ps, timeout)


def disconnect(timeout: int = 60) -> tuple[bool, str]:
    r = _api_act("/disconnect", timeout=timeout)
    if r is not None:
        return r
    ps = (
        f'Set-ItemProperty "{REGBASE}" -Name PowerOn -Value 0 -Type DWord;'
        'Stop-Service RvControlSvc -Force;'
        'Start-Sleep 2;'
        'if((Get-Service RvControlSvc).Status -eq "Stopped"){Write-Output "<<<ACTOK>>>"}'
    )
    return _run_ps(ps, timeout)


def rename_node(new_name: str, timeout: int = 60) -> tuple[bool, str]:
    """Change the node's name (Alias) that other peers see. Persists in the registry
    and restarts the service to re-announce to the mesh."""
    safe = new_name.replace('"', "").replace("'", "").replace("`", "").replace("$", "").strip()[:63]
    if not safe:
        return False, "empty name"
    r = _api_act("/rename", {"name": safe}, timeout=timeout)
    if r is not None:
        return r
    ps = (
        f'Set-ItemProperty "{REGBASE}" -Name Alias -Value \'{safe}\' -Type String;'
        'Restart-Service RvControlSvc -Force;'
        'Start-Sleep 3;'
        f'if((Get-ItemProperty "{REGBASE}").Alias -eq \'{safe}\'){{Write-Output "<<<ACTOK>>>"}}'
    )
    return _run_ps(ps, timeout)


_GUID_RE = re.compile(r"^\{[0-9A-Fa-f-]{36}\}$")


def leave_network(guid: str, timeout: int = 60) -> tuple[bool, str]:
    """Remove the network association (leave the network). guid includes the {} braces."""
    if not _GUID_RE.match(guid or ""):
        return False, "bad network id"
    r = _api_act("/leave", {"guid": guid}, timeout=timeout)
    if r is not None:
        return r
    ps = (
        f'$k="{REGBASE}\\Networks\\{guid}";'
        'if(Test-Path $k){Remove-Item $k -Recurse -Force;'
        'Restart-Service RvControlSvc -Force;'
        'Write-Output "<<<ACTOK>>>"}else{Write-Output "not-a-member"}'
    )
    return _run_ps(ps, timeout)


def reset_identity(timeout: int = 120) -> tuple[bool, str]:
    """Give this VM a brand new Radmin node (new id, new 26.x IP, no networks)."""
    r = _api_act("/reset-identity", timeout=timeout)
    if r is not None:
        return r
    rc, blob = backend.wmi_cmd(
        "powershell -NoProfile -NonInteractive -InputFormat None -ExecutionPolicy Bypass "
        rf"-File {config.AGENT_DIR}\identity.ps1 -Reset < NUL", timeout)
    return ("<<<ACTOK>>>" in blob), blob


def show_gui(timeout: int = 20) -> tuple[bool, str]:
    """Bring the Radmin window to the front of the VM screen (before opening VNC)."""
    r = _api_act("/show-gui", timeout=timeout)
    if r is not None:
        return r
    rc, blob = backend.wmi_cmd('schtasks /run /tn "RadminAgent-ShowGui"', timeout)
    return rc == 0, blob


def ack_identity() -> None:
    """Forget the 'identity was reset' notice once the user saw it."""
    backend.api("/ack-identity", "POST", timeout=5)


def _valid_targets(ips: list[str]) -> list[str]:
    """Keep valid IPv4, dedupe, and CAP at MAX_PING. Shield against a poisoned
    roster (bad dump) that would otherwise make the sweep ping thousands of IPs."""
    out: list[str] = []
    seen: set[str] = set()
    for ip in ips:
        if ip in seen:
            continue
        try:
            ipaddress.IPv4Address(ip)
        except ValueError:
            continue
        seen.add(ip)
        out.append(ip)
        if len(out) >= MAX_PING:
            break
    return out


# ---- primary path: unprivileged ICMP, ZERO processes ----------------------
def _icmp_packet(seq: int) -> bytes:
    # ICMP echo request. On a SOCK_DGRAM/IPPROTO_ICMP socket the kernel fills in
    # the id and the checksum, so we send type=8, code=0, checksum=0, id=0.
    return struct.pack("!BBHHH", _ICMP_ECHO, 0, 0, 0, seq & 0xFFFF) + b"radmin-linux"


def _ping_sweep_icmp(ips: list[str], timeout: float = 1.0) -> dict[str, float | None] | None:
    """Ping every IP through ONE non-blocking ICMP socket -- no `ping` process at
    all, so it can never storm. Returns ip -> round trip in ms (None = no reply),
    or None if the kernel denies unprivileged ICMP (then the caller falls back
    to the bounded subprocess path)."""
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_ICMP)
    except (PermissionError, OSError):
        return None
    res: dict[str, float | None] = {ip: None for ip in ips}
    sent: dict[str, float] = {}
    try:
        s.setblocking(False)
        for i, ip in enumerate(ips):               # fire all echo requests (cheap)
            if backend._CANCEL.is_set():
                return res
            try:
                sent[ip] = time.monotonic()
                s.sendto(_icmp_packet(i), (ip, 0))
            except OSError:
                pass
        pending = set(sent)
        deadline = time.monotonic() + timeout
        while pending and time.monotonic() < deadline:
            if backend._CANCEL.is_set():
                break
            rem = deadline - time.monotonic()
            r, _, _ = select.select([s], [], [], min(0.2, max(0.0, rem)))
            if not r:
                continue
            try:
                data, addr = s.recvfrom(2048)
            except OSError:
                continue
            src = addr[0]
            if data and data[0] == 0 and src in pending:   # type 0 = echo reply
                res[src] = round((time.monotonic() - sent[src]) * 1000, 1)
                pending.discard(src)
        return res
    finally:
        s.close()


# ---- fallback: `ping` subprocess, capped by a GLOBAL semaphore -------------
# Only used where ICMP sockets are denied. The semaphore caps concurrent ping
# processes system-wide (belt-and-suspenders on top of the thread pool), so even
# this path can never storm.
_PING_GATE = threading.BoundedSemaphore(PING_WORKERS)


_RTT_RE = re.compile(r"time[=<]([\d.]+)")


def _ping1(ip: str) -> tuple[str, float | None]:
    if backend._CANCEL.is_set():
        return ip, None
    with _PING_GATE:                               # global ceiling on ping procs
        if backend._CANCEL.is_set():
            return ip, None
        # -n = no reverse DNS (26.x has no PTR and would hang the resolver).
        rc, out = backend.run_capture(["ping", "-n", "-c1", "-W1", ip], timeout=3)
    if rc != 0:
        return ip, None
    m = _RTT_RE.search(out)
    return ip, (float(m.group(1)) if m else 0.1)


def _ping_sweep_subprocess(ips: list[str]) -> dict[str, float | None]:
    res: dict[str, float | None] = {}
    with concurrent.futures.ThreadPoolExecutor(max_workers=PING_WORKERS) as ex:
        futs = [ex.submit(_ping1, ip) for ip in ips]
        for fut in concurrent.futures.as_completed(futs):
            if backend._CANCEL.is_set():
                for f in futs:
                    f.cancel()
                break
            ip, up = fut.result()
            res[ip] = up
    return res


def ping_rtt(ips: list[str]) -> dict[str, float | None]:
    """ip -> round trip in ms, None when it did not answer. Process-free ICMP
    when allowed, otherwise a bounded `ping` subprocess sweep. Both are capped
    and cancellable."""
    ips = _valid_targets(ips)
    if not ips:
        return {}
    res = _ping_sweep_icmp(ips)          # preferred: zero processes
    if res is None:
        res = _ping_sweep_subprocess(ips)  # fallback: hard-capped subprocess sweep
    return res


def ping_sweep(ips: list[str]) -> dict[str, bool]:
    """ip -> answered."""
    return {ip: rtt is not None for ip, rtt in ping_rtt(ips).items()}


if __name__ == "__main__":
    import sys
    if len(sys.argv) > 1 and sys.argv[1] == "sweep":
        print(ping_rtt(sys.argv[2:]))
    else:
        print("usage: actions.py sweep <ip> [ip...]")
