#!/usr/bin/env bash
# Opens Radmin VPN (Linux), repairing the link first. The VM starts from the UI.
SELF="$(cd "$(dirname "$(readlink -f "$0")")" && pwd)"
source "$SELF/env.sh"
VENV="$RADMIN_VENV"

if ! "$SELF/preflight.sh" --light -q 2>"$RADMIN_HOME/preflight.err"; then
  if [[ -n "${DISPLAY:-}${WAYLAND_DISPLAY:-}" ]]; then
    "$VENV/bin/python" - "$SELF/preflight.sh" "$(cat "$RADMIN_HOME/preflight.err" 2>/dev/null)" <<'PY' 2>/dev/null || true
import sys
from PySide6.QtWidgets import QApplication, QMessageBox
app = QApplication(sys.argv)
QMessageBox.critical(None, "Radmin VPN (Linux)",
    "Could not prepare the link to the VM.\n\n" + sys.argv[2] +
    "\n\nRun it in a terminal for details:\n  " + sys.argv[1])
PY
  fi
  cat "$RADMIN_HOME/preflight.err" >&2
  exit 1
fi
# low I/O priority: the member sync (~200 MB dump) never stalls the desktop
IONICE=(); command -v ionice >/dev/null && IONICE=(ionice -c2 -n6)
exec "${IONICE[@]}" "$VENV/bin/python" "$SELF/app/main.py" "$@"
