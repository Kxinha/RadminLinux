#!/usr/bin/env bash
# ============================================================
#  build-vm.sh - produces the distributable base image of Radmin-Linux.
#
#  Two modes:
#   --clean SRC.qcow2   makes a release image from a ready VM (Windows + Radmin).
#                       SRC is NEVER modified: a throwaway overlay of it boots
#                       WITHOUT internet (so Radmin cannot talk to its servers),
#                       gets the current agent, runs provision/release-clean.ps1
#                       (new identity, no networks, no browser, logs, caches,
#                       neutral computer name, zeroed free space) and is frozen
#                       into dist/: qcow2.zst split in <2 GB parts + SHA256SUMS.
#   --from-scratch ISO  provisions from zero (long; needs the Windows ISO).
#
#  Before 0.3.0 --clean only removed the network list and kept the node id,
#  so the Radmin server sent the builder's networks to every download.
# ============================================================
set -uo pipefail
SELF="$(cd "$(dirname "$0")" && pwd)"
source "$SELF/env.sh"
say(){ printf '\033[1;36m>>\033[0m %s\n' "$*"; }
ok(){  printf '   \033[1;32m[ok]\033[0m %s\n' "$*"; }
die(){ printf '   \033[1;31m[ERROR]\033[0m %s\n' "$*" >&2; exit 1; }

MODE="${1:-}"; shift || true
DIST="${RADMIN_DIST:-$SELF/dist}"
IMG_NAME="radmin-linux-base.qcow2"

real_disk() {   # refuse tmpfs (RAM) for multi-GB files, check free space
  mkdir -p "$1"
  local t; t=$(stat -f -c %T "$1" 2>/dev/null)
  [[ "$t" == tmpfs || "$t" == ramfs ]] && die "$1 is on $t (RAM); use a folder on a real disk"
  local gb; gb=$(df -BG --output=avail "$1" | tail -1 | tr -dc '0-9')
  [[ -n "$gb" && "$gb" -lt "$2" ]] && die "only ${gb} GB free in $1, need ${2} GB"
  return 0
}

package_image() {   # package_image QCOW2 -> zst parts + SHA256SUMS in $DIST
  local img="$1"
  mkdir -p "$DIST"
  say "Compressing (zstd -T0 -12 --long=27)"
  rm -f "$DIST/$IMG_NAME.zst" "$DIST/$IMG_NAME.zst.part-"*
  zstd -q -T0 -12 --long=27 -f "$img" -o "$DIST/$IMG_NAME.zst" || die "zstd failed"
  split -b 1900M "$DIST/$IMG_NAME.zst" "$DIST/$IMG_NAME.zst.part-" || die "split failed"
  rm -f "$DIST/$IMG_NAME.zst"
  (cd "$DIST" && sha256sum "$IMG_NAME.zst.part-"* > SHA256SUMS)
  ok "release parts: $(cd "$DIST" && ls "$IMG_NAME.zst.part-"* | tr '\n' ' ')"
  ok "checksums in $DIST/SHA256SUMS"
}

case "$MODE" in
  --clean)
    SRC="${1:-$RADMIN_VMDIR/bench.qcow2}"
    [[ -f "$SRC" ]] || die "image not found: $SRC"
    SRC="$(readlink -f "$SRC")"
    BDIR="${RADMIN_BUILD:-$SELF/build}/clean"
    real_disk "$(dirname "$BDIR")" 30
    if "$RADMIN_PY" "$SELF/app/vmctl.py" status 2>/dev/null | grep -q running; then
      die "turn your Radmin VM off first (the build VM needs the $RADMIN_TAP link)"
    fi
    ip link show "$RADMIN_TAP" >/dev/null 2>&1 || nmcli con up "$RADMIN_NMCON" >/dev/null 2>&1 \
      || die "link $RADMIN_TAP missing (run install.sh)"
    rm -rf "$BDIR"; mkdir -p "$BDIR"
    say "1/6  Throwaway overlay of $SRC (the source stays untouched)"
    qemu-img create -q -f qcow2 -F qcow2 -b "$SRC" "$BDIR/work.qcow2" || die "overlay failed"

    say "2/6  Booting it offline (no internet: Radmin cannot register)"
    ( setsid qemu-system-x86_64 -name radmin-clean -machine q35,accel=kvm,hpet=off \
        -cpu host,hv-relaxed,hv-vapic,hv-spinlocks=0x1fff,hv-time -smp 2 -m 2048 \
        -drive file="$BDIR/work.qcow2",if=none,id=hd0,format=qcow2,cache=unsafe,discard=unmap,detect-zeroes=unmap \
        -device ich9-ahci,id=ahci -device ide-hd,drive=hd0,bus=ahci.0,rotation_rate=1 \
        -boot order=c -device VGA,vgamem_mb=16 \
        -netdev user,id=n0,restrict=on -device e1000e,netdev=n0 \
        -netdev tap,id=n1,ifname="$RADMIN_TAP",script=no,downscript=no \
        -device e1000e,netdev=n1,mac="$RADMIN_ISO_MAC" \
        -rtc base=localtime -usb -device usb-tablet -display none -vnc 127.0.0.1:6 \
        -pidfile "$BDIR/qemu.pid" -monitor unix:"$BDIR/mon.sock",server,nowait \
        >"$BDIR/qemu.log" 2>&1 < /dev/null & )
    for i in $(seq 1 15); do [[ -s "$BDIR/qemu.pid" ]] && break; sleep 1; done
    QPID="$(cat "$BDIR/qemu.pid" 2>/dev/null)"
    [[ -n "$QPID" ]] || die "build VM did not start: $(cat "$BDIR/qemu.log")"
    trap '[[ -n "${QPID:-}" ]] && kill "$QPID" 2>/dev/null' EXIT
    wmi(){ "$RADMIN_PY" "$RADMIN_VENV/bin/wmiexec.py" "$RADMIN_TARGET" "$*" 2>&1; }
    for i in $(seq 1 60); do wmi "cmd /c echo up" | grep -q '^up' && break; sleep 5; done
    wmi "cmd /c echo up" | grep -q '^up' || die "the build VM never answered WMI (VNC 127.0.0.1:5906)"
    ok "Windows up"

    say "3/6  Installing agent $(cat "$SELF/agent/VERSION")"
    RADMIN_VMDIR="$BDIR" "$RADMIN_PY" "$SELF/app/agent.py" deploy || die "agent install failed"

    say "4/6  Cleaning in two boots (identity, networks, browser, logs, journal, free space)"
    # what must NOT survive: read it from the VM now, check the frozen image later
    wmi 'reg query "HKLM\SOFTWARE\Wow6432Node\Famatech\RadminVPN\1.0" /s' | "$RADMIN_PY" -c '
import re, sys
t = sys.stdin.read()
out = set()
for name, val in re.findall(r"^\s+(RID|IPv4)\s+REG_\w+\s+(\S+)", t, re.M):
    n = int(val, 16) if val.startswith("0x") else None
    out.add(str(n) if n is not None else val)
    if name == "IPv4" and n is not None:   # dword, also look for the dotted form
        out.add(".".join(str(n >> s & 255) for s in (24, 16, 8, 0)))
out.update(re.findall(r"\\Networks\\(\{[0-9A-Fa-f-]{36}\})", t))
print("\n".join(sorted(out)))' > "$BDIR/must-not-leak.txt"
    OLDNAME="$(wmi 'cmd /c hostname' | tr -d '\r' | grep -vE '^Impacket|^\[|^$' | head -1)"
    ok "$(wc -l < "$BDIR/must-not-leak.txt") identity strings to check for afterwards"
    "$RADMIN_PY" - "$SELF/provision/release-clean.ps1" <<PY || die "could not copy the cleaner"
import io, sys
from impacket.smbconnection import SMBConnection
u, p = "$RADMIN_CRED".split(":", 1)
c = SMBConnection("$RADMIN_HOST", "$RADMIN_HOST", timeout=60); c.login(u, p)
c.putFile("C\$", "\\\\radmin-agent\\\\release-clean.ps1", io.BytesIO(open(sys.argv[1], "rb").read()).read)
c.close()
PY
    clean_stage(){
      local o; o="$(timeout 2400 "$RADMIN_PY" "$RADMIN_VENV/bin/wmiexec.py" "$RADMIN_TARGET" \
        "powershell -NoProfile -NonInteractive -InputFormat None -ExecutionPolicy Bypass -File C:\\radmin-agent\\release-clean.ps1 -Stage $1 < NUL" 2>&1)"
      echo "$o" | grep -vE '^Impacket|^\[\*\]|^$|CLEANOK' | sed 's/^/     /'
      echo "$o" | grep -q CLEANOK
    }
    clean_stage 1 || die "cleaning stage 1 did not finish"
    say "     rebooting (new computer name, compact registry)"
    wmi 'cmd /c shutdown /r /t 0 /f' >/dev/null
    sleep 20
    for i in $(seq 1 60); do wmi "cmd /c echo up" | grep -q '^up' && break; sleep 5; done
    wmi "cmd /c echo up" | grep -q '^up' || die "the build VM did not come back after the reboot"
    sleep 20
    clean_stage 2 || die "cleaning stage 2 did not finish"
    wmi 'cmd /c del /f /q C:\radmin-agent\release-clean.ps1' >/dev/null

    say "5/6  Powering off"
    wmi 'cmd /c shutdown /s /t 0 /f' >/dev/null
    for i in $(seq 1 90); do kill -0 "$QPID" 2>/dev/null || break; sleep 2; done
    kill -0 "$QPID" 2>/dev/null && { kill "$QPID"; sleep 3; }
    trap - EXIT

    say "6/6  Freezing the image"
    qemu-img convert -O qcow2 "$BDIR/work.qcow2" "$BDIR/$IMG_NAME" || die "convert failed"
    ok "image: $(du -h "$BDIR/$IMG_NAME" | cut -f1) on disk"
    say "     checking the image for the old identity"
    "$RADMIN_PY" - "$BDIR/$IMG_NAME" "$BDIR/must-not-leak.txt" <<'PY' || die "the image still contains identity data (see above); not packaged"
import collections, mmap, re, sys
pats = [l.strip() for l in open(sys.argv[2]) if l.strip()]
with open(sys.argv[1], "r+b") as f:
    m = mmap.mmap(f.fileno(), 0, access=mmap.ACCESS_WRITE)
    for p in pats:
        for enc in ("latin1", "utf-16-le"):
            b = p.encode(enc)
            if "." in p:
                rs = "0.0.0.0".encode(enc)
                rep = rs + b"\x00" * (len(b) - len(rs))
            elif p.startswith("{") and p.endswith("}"):
                rep = "{00000000-0000-0000-0000-000000000000}".encode(enc)
            else:
                rep = b"0" * len(b)
            for x in re.finditer(re.escape(b), m, re.I):
                pos = x.start()
                m[pos:pos+len(b)] = rep
    m.flush()
    m.close()
f = open(sys.argv[1], "rb")
m = mmap.mmap(f.fileno(), 0, access=mmap.ACCESS_READ)
alts = {}
for p in pats:
    for enc in ("latin1", "utf-16-le"):
        alts[p.encode(enc).lower()] = f"{p[:4]}... ({enc})"
rx = re.compile(b"|".join(re.escape(a) for a in sorted(alts, key=len, reverse=True)), re.I)
hits = collections.Counter(alts[x.group().lower()] for x in rx.finditer(m))
for k, n in hits.items():
    print(f"   leak: {k} x{n}")
bad = sum(hits.values())
print(f"   {len(pats)} strings checked, {bad} hits")
sys.exit(1 if bad else 0)
PY
    rm -f "$BDIR/must-not-leak.txt"
    if [[ -n "$OLDNAME" ]]; then
      "$RADMIN_PY" - "$BDIR/$IMG_NAME" "$OLDNAME" <<'PY'
import mmap, re, sys
f = open(sys.argv[1], "rb")
m = mmap.mmap(f.fileno(), 0, access=mmap.ACCESS_READ)
n = sum(len(re.findall(re.escape(sys.argv[2].encode(e)), m, re.I)) for e in ("latin1", "utf-16-le"))
print(f"   old computer name still in Windows internals: {n} places (not identity data)")
PY
    fi
    package_image "$BDIR/$IMG_NAME"
    rm -f "$BDIR/work.qcow2"
    say "Done. Test it before publishing: ./install.sh $BDIR/$IMG_NAME"
    ;;
  --from-scratch)
    # provision the VM from ZERO: unattended POSReady7 -> Radmin -> ICS -> agent.
    WINISO="${1:-${RADMIN_WIN_ISO:-}}"
    [[ -f "$WINISO" ]] || die "pass the Windows ISO: ./build-vm.sh --from-scratch POSReady7.iso"
    command -v qemu-img >/dev/null || die "qemu-img missing"
    command -v xorriso  >/dev/null || die "xorriso missing (install xorriso)"
    for t in mkfs.vfat mcopy; do command -v $t >/dev/null || die "$t missing (install mtools/dosfstools)"; done

    BDIR="${RADMIN_BUILD:-$SELF/build}"
    # CRITICAL PROTECTION: the VM disk grows to ~10GB during install.
    # If BDIR is on tmpfs (/tmp is RAM on most distros), that EXHAUSTS the RAM
    # and FREEZES the machine. Refuse tmpfs.
    mkdir -p "$BDIR"
    FSTYPE=$(stat -f -c %T "$BDIR" 2>/dev/null)
    if [[ "$FSTYPE" == "tmpfs" || "$FSTYPE" == "ramfs" ]]; then
      die "BDIR ($BDIR) is on $FSTYPE (RAM). That would freeze the machine.
    Use a real disk: RADMIN_BUILD=/path/on/disk ./build-vm.sh --from-scratch ..."
    fi
    AVAIL_GB=$(df -BG --output=avail "$BDIR" 2>/dev/null | tail -1 | tr -dc '0-9')
    [[ -n "$AVAIL_GB" && "$AVAIL_GB" -lt 15 ]] && die "low space in $BDIR (${AVAIL_GB}G); needs ~15G"
    rm -rf "$BDIR"; mkdir -p "$BDIR/media/agent"
    say "1/6  Downloading Radmin VPN (onto the provisioning media)"
    RURL="https://download.radmin-vpn.com/download/files/Radmin_VPN_2.0.4899.9.exe"
    curl -fsSL "$RURL" -o "$BDIR/media/Radmin_VPN.exe" || die "failed to download Radmin"

    say "2/6  Building the media (setup-guest + agent)"
    cp "$SELF/provision/setup-guest.ps1" "$BDIR/media/"
    cp "$SELF/provision/SetupComplete.cmd" "$BDIR/media/"
    cp "$SELF/agent/"*.ps1 "$SELF/agent/"*.vbs "$SELF/agent/VERSION" "$BDIR/media/agent/"
    xorriso -as mkisofs -J -r -V PROVISION -o "$BDIR/provision.iso" "$BDIR/media" 2>/dev/null || die "ISO failed"

    say "3/6  Answer floppy (autounattend)"
    dd if=/dev/zero of="$BDIR/unattend.img" bs=1024 count=1440 status=none
    mkfs.vfat -n UNATTEND "$BDIR/unattend.img" >/dev/null
    MTOOLS_SKIP_CHECK=1 mcopy -i "$BDIR/unattend.img" "$SELF/provision/autounattend.xml" ::autounattend.xml

    say "4/6  Creating the disk (24G sparse)"
    DISK="$BDIR/bench.qcow2"
    qemu-img create -f qcow2 "$DISK" 24G >/dev/null

    say "5/6  Installing + provisioning (headless, ~15 min). Watch: vncviewer 127.0.0.1:5905"
    # 2 NICs: NAT (internet for Radmin) + the isolated one with the MAC ICS expects
    # low CPU and I/O priority: the build must never stall the desktop
    nice -n 15 ionice -c2 -n7 \
    qemu-system-x86_64 -name radmin-build -machine q35,accel=kvm -cpu host -smp 2 -m 3072 \
      -drive file="$DISK",if=none,id=hd0,format=qcow2,cache=writeback \
      -device ich9-ahci,id=ahci -device ide-hd,drive=hd0,bus=ahci.0 \
      -drive file="$WINISO",if=none,id=cd0,media=cdrom,readonly=on -device ide-cd,drive=cd0,bus=ahci.1 \
      -drive file="$BDIR/provision.iso",if=none,id=cd1,media=cdrom,readonly=on -device ide-cd,drive=cd1,bus=ahci.2 \
      -drive file="$BDIR/unattend.img",if=floppy,format=raw \
      -boot order=dc,menu=off -device VGA,vgamem_mb=32 \
      -netdev user,id=n0 -device e1000e,netdev=n0 \
      -netdev user,id=n1 -device e1000e,netdev=n1,mac=52:54:00:26:00:02 \
      -rtc base=localtime -usb -device usb-tablet \
      -display none -vnc 127.0.0.1:5 \
      -pidfile "$BDIR/build.pid" -monitor unix:"$BDIR/mon.sock",server,nowait &

    # wait for QEMU to create the pidfile (the & starts it async)
    for i in $(seq 1 15); do [[ -s "$BDIR/build.pid" ]] && break; sleep 1; done
    BPID="$(cat "$BDIR/build.pid" 2>/dev/null)"
    [[ -n "$BPID" ]] && kill -0 "$BPID" 2>/dev/null || die "the build VM did not come up (see VNC 5905)"
    # setup-guest powers the VM off when done -> we wait for the process to die.
    say "   waiting (the guest powers itself off when finished; up to ~35 min)…"
    for i in $(seq 1 105); do   # 105*20s = 35 min
      sleep 20
      kill -0 "$BPID" 2>/dev/null || { say "   guest finished and powered off"; break; }
      [[ $((i % 15)) -eq 0 ]] && say "   ... still installing ($((i*20/60)) min)"
    done

    say "6/6  Finalizing the image"
    # shut down gracefully
    printf 'system_powerdown\n' | timeout 10 socat -,ignoreeof UNIX-CONNECT:"$BDIR/mon.sock" >/dev/null 2>&1
    for i in $(seq 1 30); do kill -0 "$(cat "$BDIR/build.pid" 2>/dev/null)" 2>/dev/null || break; sleep 2; done
    kill "$(cat "$BDIR/build.pid" 2>/dev/null)" 2>/dev/null || true

    OUT="${RADMIN_DIST:-$SELF/dist}/radmin-linux-base.qcow2"
    mkdir -p "$(dirname "$OUT")"
    qemu-img convert -O qcow2 "$DISK" "$OUT" && ok "base image: $OUT ($(du -h "$OUT"|cut -f1))"
    say "Next: ./build-vm.sh --clean $OUT  (adds the agent, cleans, packages)"
    ;;
  *)
    cat <<USAGE
usage:
  ./build-vm.sh --clean [SRC.qcow2]   release image from a ready VM (SRC is not modified)
                                      -> build/clean/radmin-linux-base.qcow2 + dist/ parts
  ./build-vm.sh --from-scratch ISO    provisions Windows + Radmin from zero
USAGE
    ;;
esac
