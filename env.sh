#!/usr/bin/env bash
# env.sh - central configuration for Radmin-Linux (shell side).
# Priority: environment variables > ~/.config/radmin-linux/config.env > defaults.
# `source env.sh` at the start of each script.

_CFG="${XDG_CONFIG_HOME:-$HOME/.config}/radmin-linux/config.env"
if [[ -f "$_CFG" ]]; then
  # only KEY=VALUE lines, and never override a variable already in the environment
  while IFS='=' read -r _k _v; do
    [[ "$_k" =~ ^[A-Z_][A-Z0-9_]*$ ]] || continue
    [[ -n "${!_k+x}" ]] && continue
    _v="${_v%\"}"; _v="${_v#\"}"; _v="${_v%\'}"; _v="${_v#\'}"
    export "$_k=$_v"
  done < <(grep -E '^[A-Z_][A-Z0-9_]*=' "$_CFG")
fi
unset _k _v

# where the app code lives (this file's directory) and the install root
_HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
export RADMIN_HOME="${RADMIN_HOME:-$HOME/.local/share/radmin-linux}"
export RADMIN_VMDIR="${RADMIN_VMDIR:-$RADMIN_HOME/vm}"
export RADMIN_VENV="${RADMIN_VENV:-$RADMIN_HOME/venv}"
export RADMIN_APP="${RADMIN_APP:-$_HERE/app}"

# network / VM access
export RADMIN_HOST="${RADMIN_HOST:-192.168.137.1}"
export RADMIN_CRED="${RADMIN_CRED:-bench:bench}"
export RADMIN_TARGET="${RADMIN_TARGET:-$RADMIN_CRED@$RADMIN_HOST}"
export RADMIN_API_PORT="${RADMIN_API_PORT:-47950}"

# isolated host<->VM interface
export RADMIN_TAP="${RADMIN_TAP:-tapradmin}"
export RADMIN_NMCON="${RADMIN_NMCON:-radmin-bridge}"
export RADMIN_ISO_MAC="${RADMIN_ISO_MAC:-52:54:00:26:00:02}"
export RADMIN_TAP_IP="${RADMIN_TAP_IP:-192.168.137.2/24}"

# VM footprint, kept small so it never hurts the host: 1 GB RAM / 1 vCPU
export RADMIN_VM_RAM="${RADMIN_VM_RAM:-1024}"
export RADMIN_VM_SMP="${RADMIN_VM_SMP:-1}"

# derived
export RADMIN_PY="$RADMIN_VENV/bin/python"
