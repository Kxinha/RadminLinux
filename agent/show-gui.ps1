# ============================================================
#  show-gui.ps1 - puts the real Radmin VPN window on the VM screen, restored
#  and in front, so the VNC window opens straight on it (join/create network).
#  Runs in the logged-on user's session (task RadminAgent-ShowGui, /it).
# ============================================================
$ErrorActionPreference = "SilentlyContinue"
$exe = "C:\Program Files (x86)\Radmin VPN\RvRvpnGui.exe"
Add-Type -TypeDefinition @"
using System;
using System.Runtime.InteropServices;
public static class RlWin {
  [DllImport("user32.dll")] public static extern bool ShowWindow(IntPtr h, int c);
  [DllImport("user32.dll")] public static extern bool SetForegroundWindow(IntPtr h);
  [DllImport("user32.dll")] public static extern bool BringWindowToTop(IntPtr h);
}
"@
# a second launch of the GUI only wakes the running one (single instance)
Start-Process $exe
for ($i = 0; $i -lt 20; $i++) {
  Start-Sleep -Milliseconds 500
  $p = Get-Process RvRvpnGui | Where-Object { $_.MainWindowHandle -ne 0 } | Select-Object -First 1
  if ($p) {
    [void][RlWin]::ShowWindow($p.MainWindowHandle, 9)   # SW_RESTORE
    [void][RlWin]::BringWindowToTop($p.MainWindowHandle)
    [void][RlWin]::SetForegroundWindow($p.MainWindowHandle)
    break
  }
}
