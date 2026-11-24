#!/usr/bin/env bash
# Runs the headless Windows VM (production). Parameterized by env.sh / config.env.
#
# Tuned for a smooth Radmin on a 1 GB / 1 vCPU guest:
#  - Hyper-V enlightenments + HPET off: Windows 7 stops burning CPU on timer
#    emulation, so the VPN and the VNC window do not stutter.
#  - normal CPU priority: a lower nice value starved the VM while you played,
#    which is exactly when the VPN must answer fast. Disk I/O stays low priority.
#  - VM internet through QEMU's built-in user network (slirp). Measured on QEMU
#    11.1: idle VM ~2% of one core with slirp, ~15% with -netdev passt (its main
#    loop never sleeps), so passt is opt-in only (RADMIN_NET=passt).
#  - disk seen as an SSD with discard: Windows sends TRIM and never defrags,
#    so the qcow2 file does not keep growing.
#  - SMBIOS UUID = this install's id: the in-VM agent gives every install its
#    own Radmin identity (see agent/identity.ps1).
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
for c in "$HERE/../env.sh" "$HERE/env.sh"; do [[ -f "$c" ]] && source "$c" && break; done
: "${RADMIN_VMDIR:=$HERE}"
: "${RADMIN_TAP:=tapradmin}"
DISK="${RADMIN_VM_DISK:-$RADMIN_VMDIR/bench.qcow2}"
RAM="${VM_RAM:-${RADMIN_VM_RAM:-1024}}"; SMP="${VM_SMP:-${RADMIN_VM_SMP:-1}}"
VNC_DISPLAY="${VM_VNC:-${RADMIN_VNC_DISPLAY:-3}}"
ISO_MAC="${RADMIN_ISO_MAC:-52:54:00:26:00:02}"
[[ -f "$DISK" ]] || { echo "image not found: $DISK"; exit 1; }

# per-install UUID (generated once and stored in config.env)
UUID="${RADMIN_VM_UUID:-}"
if [[ -z "$UUID" ]]; then
  UUID="$(cat /proc/sys/kernel/random/uuid)"
  CFG="${XDG_CONFIG_HOME:-$HOME/.config}/radmin-linux/config.env"
  mkdir -p "$(dirname "$CFG")"
  echo "RADMIN_VM_UUID=$UUID" >> "$CFG"
fi
SMBIOS=()
if [[ "${RADMIN_KEEP_IDENTITY:-0}" == "1" ]]; then
  SMBIOS=(-smbios "type=11,value=radmin-linux:keep-identity")
fi

# NIC 1: the VM's own internet (what Radmin uses to reach its servers and peers)
if [[ "${RADMIN_NET:-slirp}" == "passt" ]] && command -v passt >/dev/null 2>&1; then
  NET0=(-netdev passt,id=n0)
else
  NET0=(-netdev user,id=n0)
fi
# NIC 2: the isolated link to Linux (tap), if it exists
TAPARGS=()
if ip link show "$RADMIN_TAP" >/dev/null 2>&1; then
  TAPARGS=(-netdev tap,id=n1,ifname="$RADMIN_TAP",script=no,downscript=no \
           -device e1000e,netdev=n1,mac="$ISO_MAC")
fi
echo ">> VM headless: ${RAM}MB / ${SMP} vCPU | net ${NET0[1]%%,*} | VNC 127.0.0.1:$((5900 + VNC_DISPLAY))"

rm -f "$RADMIN_VMDIR/stopping"   # left by a shutdown that did not finish
IONICE=(); command -v ionice >/dev/null && IONICE=(ionice -c2 -n7)
exec "${IONICE[@]}" qemu-system-x86_64 \
  -name "Radmin VPN (Windows)",process=radmin-vm \
  -machine q35,accel=kvm,hpet=off \
  -cpu host,hv-relaxed,hv-vapic,hv-spinlocks=0x1fff,hv-time \
  -smp "$SMP" -m "$RAM" -uuid "$UUID" "${SMBIOS[@]}" \
  -global kvm-pit.lost_tick_policy=delay \
  -rtc base=localtime,driftfix=slew \
  -drive file="$DISK",if=none,id=hd0,format=qcow2,cache=writeback,discard=unmap,detect-zeroes=unmap \
  -device ich9-ahci,id=ahci -device ide-hd,drive=hd0,bus=ahci.0,rotation_rate=1 \
  -boot order=c -device VGA,vgamem_mb=16 \
  "${NET0[@]}" -device e1000e,netdev=n0 \
  "${TAPARGS[@]}" \
  -usb -device usb-tablet \
  -display none -vnc 127.0.0.1:"$VNC_DISPLAY" \
  -pidfile "$RADMIN_VMDIR/qemu.pid" -monitor unix:"$RADMIN_VMDIR/monitor.sock",server,nowait
