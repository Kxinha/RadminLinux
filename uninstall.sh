#!/usr/bin/env bash
# Removes Radmin-Linux for this user: stops the VM or OpenRad, deletes the app,
# the VM image, the venv, the config, the menu entry and the radmin-bridge link,
# and (one pkexec) the OpenRad binary and its polkit rule.
# System packages (qemu, tigervnc, ...) are left alone; remove them with your
# package manager if nothing else uses them.
#   --keep-image   keep ~/.local/share/radmin-linux/vm (your VM Radmin identity)
# The OpenRad identity in ~/.local/state/openrad is always kept (delete it by
# hand to drop that Radmin identity for good).
set -uo pipefail
SELF="$(cd "$(dirname "$(readlink -f "$0")")" && pwd)"
[[ -f "$SELF/env.sh" ]] && source "$SELF/env.sh"
HOME_DIR="${RADMIN_HOME:-$HOME/.local/share/radmin-linux}"
CONF_DIR="${XDG_CONFIG_HOME:-$HOME/.config}/radmin-linux"
NMCON="${RADMIN_NMCON:-radmin-bridge}"
KEEP=0; [[ "${1:-}" == "--keep-image" ]] && KEEP=1

if [[ -t 0 ]]; then
  if [[ $KEEP -eq 1 ]]; then what="the app (the VM image is kept)"; else what="the app AND the VM image (your Radmin identity)"; fi
  read -r -p "Remove $what? [y/N] " a
  [[ "$a" == [yYsS]* ]] || { echo "cancelled"; exit 0; }
fi

pkill -f "$HOME_DIR/app/main.py" 2>/dev/null
[[ -x "$HOME_DIR/vm/bench-stop.sh" ]] && "$HOME_DIR/vm/bench-stop.sh" >/dev/null 2>&1
OR_DIR=/usr/local/lib/radmin-linux
OR_BIN="${RADMIN_OPENRAD_BIN:-$OR_DIR/openrad}"
OR_RULE="${RADMIN_OPENRAD_POLKIT_RULE:-/etc/polkit-1/rules.d/49-radmin-linux-openrad-$USER.rules}"
[[ -x "$OR_BIN" ]] && timeout 30 "$OR_BIN" stop >/dev/null 2>&1
if [[ -e "$OR_DIR" || -e "$OR_RULE" ]] && [[ "${RADMIN_KEEP_OPENRAD:-0}" != 1 ]]; then
  if ! pkexec /bin/sh -c 'rm -f "$1"; rm -rf "$2"' sh "$OR_RULE" "$OR_DIR"; then
    echo "could not remove $OR_DIR and $OR_RULE (pkexec refused); remove them as root"
  fi
fi
nmcli -t -f NAME con show 2>/dev/null | grep -qx "$NMCON" && \
  { nmcli con delete "$NMCON" >/dev/null 2>&1 || pkexec nmcli con delete "$NMCON" >/dev/null 2>&1; }

rm -f "$HOME/.local/share/applications/radmin-linux.desktop" "$HOME/.local/bin/radmin-linux"
rm -f "${XDG_CONFIG_HOME:-$HOME/.config}/autostart/radmin-linux.desktop"
for d in "$HOME/.local/share/icons/hicolor/"*/apps; do rm -f "$d/radmin-linux.png"; done
rm -rf "$HOME/.cache/radmin-linux"
rm -rf "${XDG_DATA_HOME:-$HOME/.local/share}/OpenRad/logs"   # OpenRad logs (the identity stays)
if [[ $KEEP -eq 1 ]]; then
  find "$HOME_DIR" -mindepth 1 -maxdepth 1 ! -name vm -exec rm -rf {} +
  echo "removed; VM image kept in $HOME_DIR/vm"
else
  rm -rf "$HOME_DIR" "$CONF_DIR"
  echo "Radmin-Linux removed."
fi
