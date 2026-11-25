#!/usr/bin/env bash
# ============================================================
#  preflight.sh - checks and repairs the stack, then (optionally) starts the VM.
#    1. venv (impacket + PySide6)
#    2. tapradmin link (NetworkManager) + route 26.0.0.0/8
#    3. VM running                         -> bench-run.sh
#    4. agent in the VM answers            -> API, or WMI + install/upgrade
#  --light  only steps 1-2 (the UI opens at once, the VM starts on request)
#  -q       errors only
# ============================================================
set -uo pipefail

SELF="$(cd "$(dirname "$0")" && pwd)"
source "$SELF/env.sh"
VENV="$RADMIN_VENV"; VMDIR="$RADMIN_VMDIR"; TAP="$RADMIN_TAP"; NMCON="$RADMIN_NMCON"
HOST="$RADMIN_HOST"
RUN="${RADMIN_RUN:-$VMDIR/bench-run.sh}"
STOP="$VMDIR/bench-stop.sh"

QUIET=0; LIGHT=0
for a in "$@"; do
  [[ "$a" == "-q" ]] && QUIET=1
  [[ "$a" == "--light" ]] && LIGHT=1
done
ok(){ [[ $QUIET -eq 0 ]] && echo "  [ok] $*"; return 0; }
fix(){ [[ $QUIET -eq 0 ]] && echo "  [fix] $*"; return 0; }
err(){ echo "  [ERROR] $*" >&2; }
step(){ [[ $QUIET -eq 0 ]] && echo "== $* =="; return 0; }
FAIL=0

NATIVE=0; [[ "${RADMIN_ENGINE:-openrad}" == "openrad" ]] && NATIVE=1

step "1/4 venv"
MODS="impacket, PySide6.QtWidgets"; [[ $NATIVE -eq 1 ]] && MODS="PySide6.QtWidgets"
if [[ -x "$VENV/bin/python" ]] && "$VENV/bin/python" -c "import $MODS" 2>/dev/null; then
  ok "${MODS/.QtWidgets/}"
else
  err "Python environment broken in $VENV (run install.sh again)"; FAIL=1
fi

# native engine (OpenRad): no VM, no tapradmin link; OpenRad makes its own interface
if [[ $NATIVE -eq 1 ]]; then
  if [[ -x "${RADMIN_OPENRAD_BIN:-/usr/local/lib/radmin-linux/openrad}" ]]; then
    ok "native engine (OpenRad), no VM link needed"
  else
    err "OpenRad not found in ${RADMIN_OPENRAD_BIN:-/usr/local/lib/radmin-linux/openrad} (run install.sh again)"; FAIL=1
  fi
  exit $FAIL
fi

step "2/4 link $TAP"
if ! ip link show "$TAP" >/dev/null 2>&1; then
  fix "bringing up $NMCON"
  nmcli con up "$NMCON" >/dev/null 2>&1
  for i in 1 2 3 4 5; do ip link show "$TAP" >/dev/null 2>&1 && break; sleep 1; done
fi
if ip link show "$TAP" >/dev/null 2>&1; then ok "$TAP up"; else err "could not create $TAP (run install.sh again)"; FAIL=1; fi
if [[ $FAIL -eq 0 ]] && ! ip route show 26.0.0.0/8 2>/dev/null | grep -q "$TAP"; then
  fix "route 26.0.0.0/8 via $HOST"
  nmcli con mod "$NMCON" ipv4.routes "26.0.0.0/8 $HOST" >/dev/null 2>&1 || true
  nmcli con up "$NMCON" >/dev/null 2>&1 || true
fi

if [[ $LIGHT -eq 1 ]]; then
  [[ $FAIL -eq 0 ]] && exit 0
  exit 1
fi
[[ $FAIL -ne 0 ]] && { err "preflight incomplete"; exit 1; }

step "3/4 VM"
vm_pid(){ local p; p="$(cat "$VMDIR/qemu.pid" 2>/dev/null)" || return 1
          [[ -n "$p" ]] && grep -qa qemu "/proc/$p/cmdline" 2>/dev/null && echo "$p"; }
start_vm(){ ( cd "$VMDIR" && setsid nohup "$RUN" >"$VMDIR/qemu.log" 2>&1 < /dev/null & ); }
tap_carrier(){ ip link show "$TAP" 2>/dev/null | grep -q LOWER_UP; }
[[ -f "$VMDIR/bench.qcow2" || -n "${RADMIN_VM_DISK:-}" ]] || { err "no VM image in $VMDIR (run install.sh with the image)"; exit 1; }
if [[ ! -r /dev/kvm || ! -w /dev/kvm ]]; then err "no access to /dev/kvm (log out and back in after install)"; exit 1; fi
if vm_pid >/dev/null && ! tap_carrier; then
  # the tap was recreated while the VM ran: QEMU still holds the old one
  fix "VM lost the link; restarting it"
  "$STOP" >/dev/null 2>&1; sleep 1; start_vm
elif vm_pid >/dev/null; then
  ok "VM running (pid $(vm_pid))"
else
  fix "starting the VM"; start_vm
  sleep 2
  vm_pid >/dev/null || { err "the VM did not start: $(tail -3 "$VMDIR/qemu.log" 2>/dev/null)"; exit 1; }
fi

step "4/4 agent"
api_ver(){ "$VENV/bin/python" - "$HOST" "${RADMIN_API_PORT:-47950}" <<'PY' 2>/dev/null
import sys, json, http.client
try:
    c = http.client.HTTPConnection(sys.argv[1], int(sys.argv[2]), timeout=2)
    c.request("GET", "/ping"); print(json.loads(c.getresponse().read()).get("version", ""))
except Exception:
    pass
PY
}
WANT="$(cat "$SELF/agent/VERSION" 2>/dev/null)"
HAVE=""
for i in $(seq 1 45); do                 # Windows boots in ~30-90 s
  HAVE="$(api_ver)"; [[ -n "$HAVE" ]] && break
  sleep 2
done
if [[ -n "$HAVE" && "$HAVE" == "$WANT" ]]; then
  ok "agent $HAVE answering"
else
  # no API after 90 s = agent from an older release: install the new one over WMI
  fix "agent ${HAVE:-old} -> $WANT"
  for i in $(seq 1 20); do
    "$VENV/bin/python" "$RADMIN_APP/agent.py" ensure >/dev/null 2>&1 && { HAVE="$(api_ver)"; break; }
    sleep 6
  done
  [[ "$HAVE" == "$WANT" ]] && ok "agent $WANT installed" || { err "could not install the agent in the VM"; exit 1; }
fi
[[ $QUIET -eq 0 ]] && echo ">> stack ready."
exit 0
