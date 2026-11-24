# ============================================================
#  gui-reader.ps1 - reads what the real Radmin VPN window shows and writes it
#  to C:\radmin-agent\gui-state.json for the API (/state "radmin").
#
#  Why: the registry has no network names and no members, ARP only sees peers
#  that already sent traffic, and the old GUI memory dump was ~200 MB and slow.
#  The Radmin window is a Qt Widgets app with accessibility, so UI Automation
#  returns the exact list it draws (network names, member names, member IPs)
#  in ~30 ms. Who is connected (and how: direct UDP/TCP or relay) comes from
#  Radmin's own service.log, read incrementally.
#
#  Runs in the desktop session of the autologon user (task
#  RadminAgent-GuiReader, at logon), because UI Automation cannot see another
#  session. Keeps the Radmin window running minimized: hidden in the tray, Qt
#  drops its accessibility tree. A window the user restored (VNC) is left alone.
#  PowerShell 2.0 / C# 2 compatible.
# ============================================================
#  -Once prints one reading to stdout and exits (diagnostics; -LogFile for tests).
param([int]$Every = 3, [switch]$Once, [string]$LogFile = "")
$ErrorActionPreference = "SilentlyContinue"
$DIR  = "C:\radmin-agent"
$OUT  = "$DIR\gui-state.json"
$EXE  = "C:\Program Files (x86)\Radmin VPN\RvRvpnGui.exe"
$SLOG = "C:\ProgramData\Famatech\Radmin VPN\service.log"
if ($LogFile) { $SLOG = $LogFile; $OUT = "$DIR\gui-state-test.json" }

if (-not $Once -and -not $LogFile) {
  $mtx = New-Object System.Threading.Mutex($false, "Local\RadminLinuxGuiReader")
  if (-not $mtx.WaitOne(0)) { exit }
}

Add-Type -AssemblyName UIAutomationClient, UIAutomationTypes
Add-Type -TypeDefinition @"
using System;
using System.Collections.Generic;
using System.Runtime.InteropServices;
public static class RlGr {
  public delegate bool CB(IntPtr h, IntPtr l);
  [DllImport("user32.dll")] public static extern bool EnumWindows(CB cb, IntPtr l);
  [DllImport("user32.dll")] public static extern uint GetWindowThreadProcessId(IntPtr h, out uint p);
  [DllImport("user32.dll")] public static extern bool IsWindowVisible(IntPtr h);
  [DllImport("user32.dll")] public static extern bool IsIconic(IntPtr h);
  [DllImport("user32.dll")] public static extern bool ShowWindow(IntPtr h, int c);
  static uint want; static List<IntPtr> got;
  static bool One(IntPtr h, IntPtr l) { uint p; GetWindowThreadProcessId(h, out p); if (p == want) got.Add(h); return true; }
  public static List<IntPtr> Of(uint pid) { want = pid; got = new List<IntPtr>(); EnumWindows(new CB(One), IntPtr.Zero); return got; }
}
"@

$AE = [System.Windows.Automation.AutomationElement]
$Walker = [System.Windows.Automation.TreeWalker]::RawViewWalker

function JStr($s) {
  if ($null -eq $s) { return '""' }
  $sb = New-Object System.Text.StringBuilder
  foreach ($ch in ([string]$s).ToCharArray()) {
    $c = [int]$ch
    if ($ch -eq '"') { [void]$sb.Append('\"') }
    elseif ($ch -eq '\') { [void]$sb.Append('\\') }
    elseif ($c -lt 32 -or $c -gt 126) { [void]$sb.Append(('\u{0:x4}' -f $c)) }
    else { [void]$sb.Append($ch) }
  }
  return '"' + $sb.ToString() + '"'
}

# ---- service.log: who is connected right now ---------------------------------
$script:conn = @{}      # rid -> @{ name; via }
$script:logPos = -1L
$script:logRest = ""
function Feed-Line($l) {
  if ($l -match "Service (starting|stopping|stopped)") { $script:conn = @{}; return }
  if ($l -match "Connected to (\d+)/'(.*)' via (\w+)/") { $script:conn[$matches[1]] = @{ name = $matches[2]; via = $matches[3] }; return }
  if ($l -match "Channel to (\d+)/'(.*)' is replaced by (\w+)/") { $script:conn[$matches[1]] = @{ name = $matches[2]; via = $matches[3] }; return }
  if ($l -match "Node (\d+)/'(.*)' disconnected") { $script:conn.Remove($matches[1]); return }
}
function Read-Log {
  $fs = $null
  try {
    $share = [IO.FileShare]([int][IO.FileShare]::ReadWrite -bor [int][IO.FileShare]::Delete)
    $fs = New-Object System.IO.FileStream -ArgumentList $SLOG, ([IO.FileMode]::Open), ([IO.FileAccess]::Read), $share
    $len = $fs.Length
    if ($script:logPos -lt 0 -or $len -lt $script:logPos) {
      # first read (or the log was rotated): replay the tail, enough to cover
      # the current service run. Radmin writes UTF-16LE with a BOM.
      $script:conn = @{}; $script:logRest = ""
      $bom = New-Object byte[] 2
      $script:wide = ($fs.Read($bom, 0, 2) -eq 2 -and $bom[0] -eq 0xFF -and $bom[1] -eq 0xFE)
      $script:logPos = [Math]::Max(0L, $len - 262144L)
      if ($script:wide) { $script:logPos = [Math]::Max(2L, $script:logPos - ($script:logPos % 2)) }
    }
    if ($len -le $script:logPos) { return }
    [void]$fs.Seek($script:logPos, [IO.SeekOrigin]::Begin)
    $n = [int]($len - $script:logPos)
    $buf = New-Object byte[] $n
    $r = 0; while ($r -lt $n) { $k = $fs.Read($buf, $r, $n - $r); if ($k -le 0) { break }; $r += $k }
    if ($script:wide) {
      $r -= ($r % 2)        # never split a UTF-16 code unit
      $text = [Text.Encoding]::Unicode.GetString($buf, 0, $r)
    } else {
      $text = [Text.Encoding]::UTF8.GetString($buf, 0, $r)
    }
    $script:logPos += $r
    $text = $script:logRest + $text
    $lines = $text -split "`n"
    $script:logRest = $lines[$lines.Length - 1]
    for ($i = 0; $i -lt $lines.Length - 1; $i++) { Feed-Line $lines[$i] }
  } catch {
  } finally { if ($fs) { $fs.Close() } }
}

# ---- the Radmin window ---------------------------------------------------------
$script:mw = $null          # AutomationElement of CMainWnd
$script:mwh = [IntPtr]::Zero
$script:wokeAt = [DateTime]::MinValue
$script:wokeByUs = $false
$script:triedExpand = @{}

function Find-Main {
  $p = Get-Process RvRvpnGui | Select-Object -First 1
  if (-not $p) { return $false }
  foreach ($h in [RlGr]::Of([uint32]$p.Id)) {
    $e = $null
    try { $e = $AE::FromHandle($h) } catch {}
    if ($e -and $e.Current.ClassName -eq "CMainWnd") { $script:mw = $e; $script:mwh = $h; return $true }
  }
  return $false
}

# The window must exist and not be hidden in the tray. Started or woken by us
# = minimized right away, so the VM screen stays quiet. Right after logon the
# GUI may still be starting and ignore the wake: try again every 10 s, and
# minimize it when it shows up late.
function Ensure-Window {
  $vis = ($script:mwh -ne [IntPtr]::Zero -and [RlGr]::IsWindowVisible($script:mwh))
  if (-not $vis) { $vis = ((Find-Main) -and [RlGr]::IsWindowVisible($script:mwh)) }
  if ($vis) {
    if ($script:wokeByUs -and -not [RlGr]::IsIconic($script:mwh)) {
      [void][RlGr]::ShowWindow($script:mwh, 7)     # SW_SHOWMINNOACTIVE
      Start-Sleep -Milliseconds 300
    }
    $script:wokeByUs = $false
    return $true
  }
  if (([DateTime]::UtcNow - $script:wokeAt).TotalSeconds -lt 10) { return $false }
  $script:wokeAt = [DateTime]::UtcNow
  if ((Get-Service RvControlSvc).Status -ne "Running") { return $false }
  # a second launch only wakes the running instance (single instance app)
  Start-Process $EXE
  $script:wokeByUs = $true
  for ($i = 0; $i -lt 30; $i++) {
    Start-Sleep -Milliseconds 500
    if ((Find-Main) -and [RlGr]::IsWindowVisible($script:mwh)) {
      [void][RlGr]::ShowWindow($script:mwh, 7)     # SW_SHOWMINNOACTIVE
      Start-Sleep -Milliseconds 300
      $script:wokeByUs = $false
      return $true
    }
  }
  return $false
}

function Kids($el) {
  $r = @()
  $c = $Walker.GetFirstChild($el)
  while ($c) { $r += $c; $c = $Walker.GetNextSibling($c) }
  return $r
}

# Depth-first walk that only keeps what the app needs.
function Walk($el, $ctx, $d) {
  if ($d -gt 30) { return }
  $c = $el.Current
  $cls = $c.ClassName
  if ($cls -eq "CNetworkWidget") {
    $net = @{ name = ""; members = New-Object System.Collections.ArrayList; toggle = $null }
    [void]$ctx.nets.Add($net)
    foreach ($k in (Kids $el)) { Walk $k @{ nets = $ctx.nets; net = $net; node = $null; top = $ctx.top } ($d + 1) }
    return
  }
  if ($cls -eq "CNetworkWidgetHeader" -and $ctx.net) {
    foreach ($k in (Kids $el)) {
      $kc = $k.Current
      if ($kc.ClassName -eq "QElideLabel" -and -not $ctx.net.name) { $ctx.net.name = $kc.Name }
      elseif ($kc.ControlType -eq [System.Windows.Automation.ControlType]::CheckBox) { $ctx.net.toggle = $k }
    }
    return
  }
  if ($cls -eq "CNodeWidget" -and $ctx.net) {
    $m = @{ name = ""; ip = "" }
    foreach ($k in (Kids $el)) {
      $kc = $k.Current
      if ($kc.ClassName -eq "QElideLabel" -and -not $m.name) { $m.name = $kc.Name }
      elseif ($kc.ClassName -eq "QLabel" -and $kc.Name -match '^26\.\d+\.\d+\.\d+$') { $m.ip = $kc.Name }
    }
    if ($m.ip -or $m.name) { [void]$ctx.net.members.Add($m) }
    return
  }
  if (-not $ctx.net -and $c.ControlType -eq [System.Windows.Automation.ControlType]::Text -and $c.Name) {
    [void]$ctx.top.Add($c.Name)
  }
  foreach ($k in (Kids $el)) { Walk $k $ctx ($d + 1) }
}

function Read-Gui {
  $ctx = @{ nets = New-Object System.Collections.ArrayList; net = $null; node = $null; top = New-Object System.Collections.ArrayList }
  Walk $script:mw $ctx 0
  return $ctx
}

# Collapsed networks normally still list their members to UI Automation; if a
# collapsed one (toggle Off) shows none, expand it once and look again. An
# expanded network (On) is never touched, so an empty network stays as it is.
function Expand-Collapsed($ctx) {
  $did = $false
  foreach ($n in $ctx.nets) {
    if ($n.members.Count -gt 0 -or -not $n.toggle -or $script:triedExpand[$n.name]) { continue }
    $script:triedExpand[$n.name] = $true
    try {
      $tp = $n.toggle.GetCurrentPattern([System.Windows.Automation.TogglePattern]::Pattern)
      if ($tp -and $tp.Current.ToggleState -eq [System.Windows.Automation.ToggleState]::Off) { $tp.Toggle(); $did = $true }
    } catch {}
  }
  return $did
}

# What the list looks like, to tell a settled window from one still loading.
function Signature($ctx) {
  $sb = New-Object System.Text.StringBuilder
  foreach ($n in $ctx.nets) {
    [void]$sb.Append("[" + $n.name + "]")
    foreach ($m in $n.members) { [void]$sb.Append($m.name + "=" + $m.ip + ";") }
  }
  foreach ($t in $ctx.top) { [void]$sb.Append("|" + $t) }
  return $sb.ToString()
}

function Build-Json($ctx, $ok, $err) {
  $online = @{}
  foreach ($v in $script:conn.Values) { $online[$v.name] = $v.via }
  $nets = @()
  if ($ctx) {
    foreach ($n in $ctx.nets) {
      $ms = @()
      foreach ($m in $n.members) {
        $via = ""; if ($online.ContainsKey($m.name)) { $via = [string]$online[$m.name] }
        $on = 'false'; if ($via) { $on = 'true' }
        $ms += '{"name":' + (JStr $m.name) + ',"ip":' + (JStr $m.ip) + ',"online":' + $on + ',"via":' + (JStr $via) + '}'
      }
      $nets += '{"name":' + (JStr $n.name) + ',"members":[' + ($ms -join ',') + ']}'
    }
  }
  $alias = ""; $ip = ""; $status = ""
  if ($ctx) {
    $t = @($ctx.top)
    for ($i = 0; $i -lt $t.Count; $i++) {
      if ($t[$i] -match '^26\.\d+\.\d+\.\d+$') {
        $ip = $t[$i]
        if ($i -gt 0) { $alias = $t[$i - 1] }
        if ($i + 1 -lt $t.Count) { $status = $t[$i + 1] }
        break
      }
    }
  }
  $okS = 'false'; if ($ok) { $okS = 'true' }
  return '{"ok":' + $okS + ',"alias":' + (JStr $alias) + ',"ip":' + (JStr $ip) + ',"status":' + (JStr $status) +
         ',"connected":' + $script:conn.Count + ',"networks":[' + ($nets -join ',') + '],"error":' + (JStr $err) + '}'
}

$last = ""; $lastWrite = [DateTime]::MinValue
$tmp = "$OUT.tmp"
# Only publish once the window has settled: right after it appears (boot,
# wake, Radmin restart) it can still be filling in. The first reading must
# repeat unchanged before the app sees it; after that changes go out at once.
$settled = [bool]$Once; $prevSig = $null
while ($true) {
  $t0 = [DateTime]::UtcNow
  Read-Log
  $ctx = $null; $ok = $false; $err = ""
  if (Ensure-Window) {
    try {
      $ctx = Read-Gui
      if (Expand-Collapsed $ctx) { Start-Sleep -Milliseconds 400; $ctx = Read-Gui }
      $sig = Signature $ctx
      if (-not $settled -and ($sig -ne $prevSig -or -not $ctx.top.Count)) {
        $prevSig = $sig; $ctx = $null; $err = "reading the Radmin window"
        Start-Sleep -Seconds 1; continue
      } else {
        $settled = $true; $prevSig = $sig; $ok = $true
      }
    } catch { $err = "ui automation: " + $_.Exception.Message; $script:mwh = [IntPtr]::Zero; $settled = $false; $prevSig = $null }
  } else {
    $err = "Radmin window not available"; $settled = $false; $prevSig = $null
  }
  $body = Build-Json $ctx $ok $err
  if ($Once) { Write-Output $body; if ($LogFile) { $Error | % { "ERR " + $_ } }; break }
  # write on change, and every 10 s as a heartbeat (the API checks the age)
  if ($body -ne $last -or ($t0 - $lastWrite).TotalSeconds -ge 10) {
    try {
      [IO.File]::WriteAllText($tmp, $body)
      if (Test-Path $OUT) { [IO.File]::Delete($OUT) }
      [IO.File]::Move($tmp, $OUT)
      $last = $body; $lastWrite = $t0
    } catch {}
  }
  if ((Get-Random -Maximum 40) -eq 0) { [GC]::Collect() }
  Start-Sleep -Seconds $Every
}
