#!/usr/bin/env bash
# Removes Radmin-Linux for this user: stops the VM, deletes the app, the VM
# image, the venv, the config, the menu entry and the radmin-bridge link.
# System packages (qemu, tigervnc, ...) are left alone; remove them with your
# package manager if nothing else uses them.
#   --keep-image   keep ~/.local/share/radmin-linux/vm (your Radmin identity)
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
nmcli -t -f NAME con show 2>/dev/null | grep -qx "$NMCON" && \
  { nmcli con delete "$NMCON" >/dev/null 2>&1 || pkexec nmcli con delete "$NMCON" >/dev/null 2>&1; }

rm -f "$HOME/.local/share/applications/radmin-linux.desktop" "$HOME/.local/bin/radmin-linux"
for d in "$HOME/.local/share/icons/hicolor/"*/apps; do rm -f "$d/radmin-linux.png"; done
rm -rf "$HOME/.cache/radmin-linux"
if [[ $KEEP -eq 1 ]]; then
  find "$HOME_DIR" -mindepth 1 -maxdepth 1 ! -name vm -exec rm -rf {} +
  echo "removed; VM image kept in $HOME_DIR/vm"
else
  rm -rf "$HOME_DIR" "$CONF_DIR"
  echo "Radmin-Linux removed."
fi
