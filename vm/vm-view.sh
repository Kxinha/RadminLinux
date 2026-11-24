#!/usr/bin/env bash
# Opens the VM screen (the real Radmin window) in a VNC viewer.
# Shared: several viewers can be open; no remote resize (Windows 7 has no
# dynamic resolution, resizing made the picture jump); raw encoding is the
# fastest on localhost and avoids the JPEG smearing of the defaults.
HERE="$(cd "$(dirname "$0")" && pwd)"
for c in "$HERE/../env.sh" "$HERE/env.sh"; do [[ -f "$c" ]] && source "$c" && break; done
D="${VM_VNC:-${RADMIN_VNC_DISPLAY:-3}}"
ADDR="127.0.0.1::$((5900 + D))"
if vncviewer --help 2>&1 | grep -q RemoteResize; then     # TigerVNC
  exec vncviewer -Shared -RemoteResize=0 -AutoSelect=0 -PreferredEncoding=Raw \
       -FullColor "$ADDR"
fi
exec vncviewer "$ADDR"
