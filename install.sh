#!/usr/bin/env bash
# ============================================================
#  install.sh - installs Radmin-Linux for the current user.
#
#    ./install.sh [image.qcow2 | image.qcow2.zst]
#
#   1. checks KVM and /dev/kvm access
#   2. installs the missing system packages (ONE pkexec prompt)
#   3. Python venv (impacket + PySide6-Essentials)
#   4. code into ~/.local/share/radmin-linux
#   5. VM image (only when given, or when none is installed yet)
#   6. isolated host<->VM link (NetworkManager tap + route to 26.0.0.0/8)
#   7. config.env (keeps your per-install id and settings on upgrades)
#   8. menu entry + icon
#  Re-running it upgrades the app and keeps your VM image and identity.
#  Log: ~/.local/share/radmin-linux/install.log
# ============================================================
set -uo pipefail

SELF="$(cd "$(dirname "$0")" && pwd)"
VERSION="$(sed -n 's/^VERSION = "\(.*\)"/\1/p' "$SELF/app/config.py")"
HOME_DIR="${RADMIN_HOME:-$HOME/.local/share/radmin-linux}"
VMDIR="$HOME_DIR/vm"
VENV="$HOME_DIR/venv"
CONF_DIR="${XDG_CONFIG_HOME:-$HOME/.config}/radmin-linux"
CONF="$CONF_DIR/config.env"
TAP="${RADMIN_TAP:-tapradmin}"
NMCON="${RADMIN_NMCON:-radmin-bridge}"
TAP_IP="${RADMIN_TAP_IP:-192.168.137.2/24}"
VM_IMAGE="${1:-${RADMIN_VM_IMAGE:-}}"

mkdir -p "$HOME_DIR"
LOG="$HOME_DIR/install.log"
exec > >(tee -a "$LOG") 2>&1
echo "=== install $VERSION $(date -Iseconds) ==="

say(){ printf '\033[1;36m>>\033[0m %s\n' "$*"; }
ok(){  printf '   \033[1;32m[ok]\033[0m %s\n' "$*"; }
warn(){ printf '   \033[1;33m[!]\033[0m %s\n' "$*"; }
die(){ printf '   \033[1;31m[ERROR]\033[0m %s\n' "$*" >&2; echo "   log: $LOG" >&2; exit 1; }

[[ $EUID -eq 0 ]] && die "run as your normal user, not root (it asks for the password when needed)"

# ---------- 1. KVM ----------
say "1/8  KVM"
grep -qE 'vmx|svm' /proc/cpuinfo || die "this CPU has no hardware virtualization (VT-x / AMD-V)"
ROOT_CMDS=()          # everything that needs root, run in ONE pkexec prompt
if [[ ! -e /dev/kvm ]]; then
  if grep -q vmx /proc/cpuinfo; then MOD=kvm_intel; else MOD=kvm_amd; fi
  ROOT_CMDS+=("modprobe $MOD")
fi
if [[ -e /dev/kvm && ! ( -r /dev/kvm && -w /dev/kvm ) ]] || [[ ! -e /dev/kvm ]]; then
  getent group kvm >/dev/null && ! id -nG | grep -qw kvm && ROOT_CMDS+=("usermod -aG kvm $USER")
fi

# ---------- 2. system packages ----------
say "2/8  System packages"
# command -> package, per family. Only what is missing gets installed.
#   qemu (x86_64 system emulator + qemu-img), zstd (image), socat (VM monitor),
#   vncviewer (the Radmin window), nmcli (the link), python3
if command -v pacman >/dev/null; then
  FAM=arch;  INST="pacman -S --needed --noconfirm"
  P_QEMU=qemu-base; P_IMG=qemu-img; P_VNC=tigervnc; P_NM=networkmanager; P_PY=python; P_VENV=""
elif command -v apt-get >/dev/null; then
  FAM=debian; INST="apt-get install -y --no-install-recommends"
  P_QEMU=qemu-system-x86; P_IMG=qemu-utils; P_VNC=tigervnc-viewer; P_NM=network-manager; P_PY=python3; P_VENV=python3-venv
elif command -v dnf >/dev/null; then
  FAM=fedora; INST="dnf install -y --setopt=install_weak_deps=False"
  P_QEMU=qemu-system-x86-core; P_IMG=qemu-img; P_VNC=tigervnc; P_NM=NetworkManager; P_PY=python3; P_VENV=""
elif command -v zypper >/dev/null; then
  FAM=suse;  INST="zypper --non-interactive install --no-recommends"
  P_QEMU=qemu-x86; P_IMG=qemu-tools; P_VNC=tigervnc; P_NM=NetworkManager; P_PY=python3; P_VENV=""
else
  die "package manager not recognized. Install by hand: qemu (x86_64), qemu-img, zstd, socat, a VNC viewer (tigervnc), NetworkManager, python3 with venv"
fi
need=()
command -v qemu-system-x86_64 >/dev/null || need+=("$P_QEMU")
command -v qemu-img  >/dev/null || need+=("$P_IMG")
command -v zstd      >/dev/null || need+=(zstd)
command -v socat     >/dev/null || need+=(socat)
command -v vncviewer >/dev/null || need+=("$P_VNC")
command -v nmcli     >/dev/null || need+=("$P_NM")
command -v python3   >/dev/null || need+=("$P_PY")
if [[ -n "$P_VENV" ]] && ! python3 -c "import ensurepip, venv" 2>/dev/null; then need+=("$P_VENV"); fi
need=($(printf '%s\n' "${need[@]}" | awk '!s[$0]++'))
if [[ ${#need[@]} -gt 0 ]]; then
  say "   missing: ${need[*]}"
  PRE=""; [[ $FAM == debian ]] && PRE="apt-get update -qq && "
  ROOT_CMDS=("${PRE}$INST ${need[*]}" "${ROOT_CMDS[@]}")
fi
if [[ ${#ROOT_CMDS[@]} -gt 0 ]]; then
  joined="$(printf '%s && ' "${ROOT_CMDS[@]}")"; joined="${joined% && }"
  say "   asking for your password once (pkexec) for: ${joined}"
  pkexec sh -c "$joined" || die "the system step failed (see above)"
fi
for c in qemu-system-x86_64 qemu-img zstd socat vncviewer nmcli python3; do
  command -v "$c" >/dev/null || die "$c is still missing"
done
[[ -r /dev/kvm && -w /dev/kvm ]] && ok "/dev/kvm accessible" || \
  warn "/dev/kvm needs the kvm group: log out and back in once before starting the VM"

# ---------- 3. venv ----------
say "3/8  Python environment"
PYMODS="import impacket, PySide6.QtWidgets"
if [[ -x "$VENV/bin/python" ]] && "$VENV/bin/python" -c "$PYMODS" 2>/dev/null; then
  ok "venv already present"
else
  # PySide6-Essentials = QtCore/Gui/Widgets only (about 1/4 of full PySide6)
  if command -v uv >/dev/null; then
    uv venv -q "$VENV" && VIRTUAL_ENV="$VENV" uv pip install -q impacket PySide6-Essentials
  else
    python3 -m venv "$VENV" && "$VENV/bin/pip" install -q --disable-pip-version-check impacket PySide6-Essentials
  fi
  "$VENV/bin/python" -c "$PYMODS" 2>/dev/null && ok "venv ready" || die "could not create the venv in $VENV"
fi
if [[ ! -e "$VENV/bin/wmiexec.py" ]]; then
  WM="$(find "$VENV" -name wmiexec.py -path '*examples*' 2>/dev/null | head -1)"
  [[ -z "$WM" ]] && WM="$(find "$VENV/bin" -name 'wmiexec*' 2>/dev/null | head -1)"
  [[ -n "$WM" ]] && ln -sf "$WM" "$VENV/bin/wmiexec.py"
fi
[[ -e "$VENV/bin/wmiexec.py" ]] || die "impacket's wmiexec.py not found in the venv"

# ---------- 4. code ----------
say "4/8  App $VERSION"
mkdir -p "$HOME_DIR/app" "$HOME_DIR/vm"
rm -rf "$HOME_DIR/app/"*.py "$HOME_DIR/app/__pycache__" "$HOME_DIR/agent"
cp "$SELF/app/"*.py "$HOME_DIR/app/"
cp -r "$SELF/shim" "$SELF/agent" "$HOME_DIR/"
cp "$SELF/env.sh" "$SELF/preflight.sh" "$SELF/radmin-linux.sh" "$SELF/uninstall.sh" \
   "$SELF/deploy-agent.sh" "$HOME_DIR/"
rm -f "$HOME_DIR/deploy-shim.sh"
cp "$SELF/vm/vm-run.sh"  "$VMDIR/bench-run.sh"
cp "$SELF/vm/vm-view.sh" "$VMDIR/bench-view.sh"
cp "$SELF/vm/vm-stop.sh" "$VMDIR/bench-stop.sh"
chmod +x "$HOME_DIR"/*.sh "$VMDIR"/bench-*.sh
echo "$VERSION" > "$HOME_DIR/VERSION"
ok "code in $HOME_DIR"

# ---------- 5. VM image ----------
say "5/8  VM image"
IMG="$VMDIR/bench.qcow2"
NEW_IMAGE=0
if [[ -n "$VM_IMAGE" ]]; then
  [[ -f "$VM_IMAGE" ]] || die "image not found: $VM_IMAGE"
  [[ -f "$IMG" ]] && { mv -f "$IMG" "$IMG.old"; warn "previous image kept as $IMG.old (delete it when the new one works)"; }
  say "   importing $(basename "$VM_IMAGE")…"
  case "$VM_IMAGE" in
    *.qcow2.zst) zstd -d -q --long=31 -o "$IMG.part" "$VM_IMAGE" ;;
    *.qcow2)     if [[ "${RADMIN_IMAGE_MOVE:-0}" == 1 ]]; then mv "$VM_IMAGE" "$IMG.part"; else cp --sparse=always "$VM_IMAGE" "$IMG.part"; fi ;;
    *) die "unknown image format: $VM_IMAGE (want .qcow2 or .qcow2.zst)" ;;
  esac || die "could not import the image (disk full?)"
  qemu-img check -q "$IMG.part" >/dev/null 2>&1 || die "the image is damaged; download it again"
  mv "$IMG.part" "$IMG"
  NEW_IMAGE=1
  ok "image imported"
elif [[ -f "$IMG" ]]; then
  ok "image already installed (kept)"
else
  warn "no VM image yet. Use bootstrap.sh, or: $HOME_DIR/install.sh /path/radmin-linux-base.qcow2.zst"
fi

# ---------- 6. isolated link ----------
say "6/8  Host <-> VM link ($TAP)"
# zone "block": the VM can reach nothing on this PC (replies to our own
# connections still pass). Before 0.3.0 it was "trusted", which exposed every
# local service to the VM and, through it, to the Radmin network.
ZONE=block
NMARGS=(tun.vnet-hdr yes ipv4.method manual ipv4.addresses "$TAP_IP" ipv4.routes "26.0.0.0/8 192.168.137.1"
        ipv4.never-default yes ipv4.ignore-auto-dns yes ipv6.method disabled
        connection.zone "$ZONE" connection.autoconnect yes)
if nmcli -t -f NAME con show | grep -qx "$NMCON"; then
  nmcli con mod "$NMCON" "${NMARGS[@]}" 2>/dev/null || pkexec nmcli con mod "$NMCON" "${NMARGS[@]}" \
    || die "could not update the $NMCON connection"
else
  nmcli con add type tun con-name "$NMCON" ifname "$TAP" mode tap owner "$(id -u)" "${NMARGS[@]}" >/dev/null 2>&1 \
    || pkexec nmcli con add type tun con-name "$NMCON" ifname "$TAP" mode tap owner "$(id -u)" "${NMARGS[@]}" >/dev/null \
    || die "could not create the $NMCON connection"
fi
if ! ip -4 addr show "$TAP" 2>/dev/null | grep -qF "${TAP_IP%/*}"; then
  nmcli con up "$NMCON" >/dev/null 2>&1 || true
fi
ip link show "$TAP" >/dev/null 2>&1 && ok "$TAP up ($TAP_IP, 26.0.0.0/8 via the VM, zone $ZONE)" \
  || warn "$TAP not up yet; the app retries when it starts"

# ---------- 7. config ----------
say "7/8  Config"
mkdir -p "$CONF_DIR"
keep(){ [[ -f "$CONF" ]] && sed -n "s/^$1=//p" "$CONF" | tail -1; }
UUID="$(keep RADMIN_VM_UUID)"; [[ -z "$UUID" ]] && UUID="$(cat /proc/sys/kernel/random/uuid)"
KEEPID="$(keep RADMIN_KEEP_IDENTITY)"; [[ -n "${RADMIN_KEEP_IDENTITY:-}" ]] && KEEPID="$RADMIN_KEEP_IDENTITY"
RAM="$(keep RADMIN_VM_RAM)"; SMP="$(keep RADMIN_VM_SMP)"; NET="$(keep RADMIN_NET)"
cat > "$CONF.new" <<CFG
# Radmin-Linux $VERSION - written by install.sh on $(date -Iseconds)
# Edit freely; re-running install.sh keeps the last 5 settings.
RADMIN_HOME=$HOME_DIR
RADMIN_VMDIR=$VMDIR
RADMIN_VENV=$VENV
RADMIN_APP=$HOME_DIR/app
RADMIN_TAP=$TAP
RADMIN_NMCON=$NMCON
RADMIN_TAP_IP=$TAP_IP
RADMIN_HOST=192.168.137.1
RADMIN_CRED=bench:bench
RADMIN_RUN=$VMDIR/bench-run.sh
RADMIN_VIEW=$VMDIR/bench-view.sh
# id of this install: a VM image that sees a different id gets a NEW Radmin identity
RADMIN_VM_UUID=$UUID
CFG
[[ -n "$KEEPID" ]] && echo "RADMIN_KEEP_IDENTITY=$KEEPID" >> "$CONF.new"
[[ -n "$RAM" ]] && echo "RADMIN_VM_RAM=$RAM" >> "$CONF.new"
[[ -n "$SMP" ]] && echo "RADMIN_VM_SMP=$SMP" >> "$CONF.new"
[[ -n "$NET" ]] && echo "RADMIN_NET=$NET" >> "$CONF.new"
mv "$CONF.new" "$CONF"
ok "config in $CONF"

# ---------- 8. menu entry ----------
say "8/8  Menu entry"
ICONS="$HOME/.local/share/icons/hicolor"
for sz in 16 22 24 32 48 64 128 256; do
  if [[ -f "$SELF/assets/radmin-linux-$sz.png" ]]; then
    mkdir -p "$ICONS/${sz}x${sz}/apps"
    cp "$SELF/assets/radmin-linux-$sz.png" "$ICONS/${sz}x${sz}/apps/radmin-linux.png"
  fi
done
gtk-update-icon-cache -q "$ICONS" 2>/dev/null || true
APPS="$HOME/.local/share/applications"
mkdir -p "$APPS"
sed "s|^Exec=radmin-linux$|Exec=$HOME_DIR/radmin-linux.sh|" \
    "$SELF/packaging/radmin-linux.desktop" > "$APPS/radmin-linux.desktop"
update-desktop-database "$APPS" 2>/dev/null || true
mkdir -p "$HOME/.local/bin"
ln -sf "$HOME_DIR/radmin-linux.sh" "$HOME/.local/bin/radmin-linux"
ok "'Radmin VPN' is in the menu (or run: radmin-linux)"

echo
say "Radmin-Linux $VERSION installed."
if [[ $NEW_IMAGE -eq 1 ]]; then
  say "On the first start the VM gives itself its own Radmin identity, then join your networks with Network > Join a network."
fi
say "Uninstall: $HOME_DIR/uninstall.sh"
