# ============================================================
#  api.ps1 - tiny HTTP/JSON API the Linux app talks to (port 47950).
#
#  Before: every refresh started a new wmiexec process on Linux, logged in
#  over SMB/DCOM and started PowerShell in the VM, ~10-20 s per click.
#  Now: this script stays running (scheduled task, SYSTEM) and answers in
#  well under a second. WMI stays as the fallback and for upgrades.
#
#  Only the Linux host can reach it: the firewall allows inbound only from
#  192.168.137.0/24 and every connection from anywhere else is dropped here.
#
#  GET  /ping                      {"ok":true,"version":...}
#  GET  /state                     node, service, networks, peers (ARP)
#  GET  /health[?heal=1]           health.ps1 report
#  GET  /update[?install=1]        radmin-update.ps1 report
#  POST /connect /disconnect       start/stop RvControlSvc
#  POST /rename?name=X             Radmin alias
#  POST /leave?guid={...}          leave one network
#  POST /reset-identity            brand new Radmin node (identity.ps1 -Reset)
#  POST /show-gui                  bring the Radmin window to the VM screen
#  POST /orchestrate               re-run the ICS orchestrator
#  POST /dump                      minidump of the GUI (member discovery)
#  POST /ack-identity              forget the "identity was reset" notice
#  PowerShell 2.0 compatible.
# ============================================================
param([int]$Port = 47950)
$ErrorActionPreference = "SilentlyContinue"
$DIR  = "C:\radmin-agent"
$BASE = "HKLM:\SOFTWARE\Wow6432Node\Famatech\RadminVPN\1.0"
$LOG  = "$DIR\api.log"
$VERSION = ""
if (Test-Path "$DIR\VERSION") { $VERSION = (Get-Content "$DIR\VERSION" | Select-Object -First 1).Trim() }
function Log($m){ $ts=Get-Date -Format "yyyy-MM-dd HH:mm:ss"; Add-Content $LOG "[$ts] $m" }

function JStr($s) {
  if ($null -eq $s) { return '""' }
  $s = [string]$s
  $sb = New-Object System.Text.StringBuilder
  foreach ($ch in $s.ToCharArray()) {
    $c = [int]$ch
    if ($ch -eq '"') { [void]$sb.Append('\"') }
    elseif ($ch -eq '\') { [void]$sb.Append('\\') }
    elseif ($c -lt 32 -or $c -gt 126) { [void]$sb.Append(('\u{0:x4}' -f $c)) }
    else { [void]$sb.Append($ch) }
  }
  return '"' + $sb.ToString() + '"'
}
function JBool($b){ if ($b) { 'true' } else { 'false' } }

# ---- state ------------------------------------------------------------------
$script:nb = @{}        # ip -> @{ name; t }   NetBIOS name cache
function Get-NbName($ip) {
  $now = [DateTime]::UtcNow
  $e = $script:nb[$ip]
  if ($e -and ($now - $e.t).TotalMinutes -lt 30) { return $e.name }
  if ($script:nbBudget -le 0) { if ($e) { return $e.name } else { return "" } }
  $script:nbBudget--
  $hn = ""
  try {
    $psi = New-Object System.Diagnostics.ProcessStartInfo("nbtstat.exe", "-A $ip")
    $psi.RedirectStandardOutput = $true; $psi.RedirectStandardError = $true
    $psi.UseShellExecute = $false; $psi.CreateNoWindow = $true
    $proc = [System.Diagnostics.Process]::Start($psi)
    if ($proc.WaitForExit(1200)) {
      foreach ($nl in ($proc.StandardOutput.ReadToEnd() -split "`r?`n")) {
        if ($nl -match "^\s*(\S+)\s+<00>\s+UNIQUE") { $hn = $matches[1]; break }
      }
    } else { try { $proc.Kill() } catch {} }
  } catch {}
  $script:nb[$ip] = @{ name = $hn; t = $now }
  return $hn
}

function Get-StateJson {
  $script:nbBudget = 3       # at most 3 new NetBIOS lookups per request (~3.6 s worst case)
  $nodeIp = $null
  $cfg = Get-WmiObject Win32_NetworkAdapterConfiguration -Filter "IPEnabled=True" | Where-Object { $_.Description -match "Radmin" } | Select-Object -First 1
  if ($cfg) { foreach ($ip in $cfg.IPAddress) { if ($ip -like "26.*") { $nodeIp = $ip } } }
  $svc = (Get-Service RvControlSvc).Status
  if (-not $svc) { $svc = "NotInstalled" }
  $peers = @()
  $inRadmin = $false
  foreach ($line in (& arp.exe -a)) {
    if ($line -match "Interface:\s+(\S+)") { $inRadmin = ($matches[1] -like "26.*"); continue }
    if (-not $inRadmin) { continue }
    if ($line -match "^\s*(26\.\d+\.\d+\.\d+)\s+([0-9a-fA-F-]{17})\s+(\w+)") {
      $ip = $matches[1]; $mac = $matches[2]; $type = $matches[3]
      if ($ip -eq "26.255.255.255" -or $ip -eq "26.0.0.1" -or $ip -eq $nodeIp) { continue }
      if ($mac -eq "ff-ff-ff-ff-ff-ff") { continue }
      $peers += '{"ip":' + (JStr $ip) + ',"mac":' + (JStr $mac) + ',"type":' + (JStr $type) + ',"host":' + (JStr (Get-NbName $ip)) + '}'
    }
  }
  $nets = @()
  if (Test-Path "$BASE\Networks") { foreach ($sub in Get-ChildItem "$BASE\Networks") { $nets += (JStr $sub.PSChildName) } }
  $alias = (Get-ItemProperty $BASE).Alias
  $note = ""
  if (Test-Path "$DIR\identity-reset.txt") { $note = (Get-Content "$DIR\identity-reset.txt" | Select-Object -First 1) }
  $gui = [bool](Get-Process RvRvpnGui)
  $ts = [int][double]::Parse((Get-Date -UFormat %s))
  return '{"ok":true,"api":' + (JStr $VERSION) + ',"node_ip":' + (JStr $nodeIp) +
         ',"hostname":' + (JStr $env:COMPUTERNAME) + ',"alias":' + (JStr $alias) +
         ',"service":' + (JStr $svc) + ',"gui":' + (JBool $gui) +
         ',"identity_reset":' + (JStr $note) +
         ',"networks":[' + ($nets -join ',') + '],"peers":[' + ($peers -join ',') + '],"ts":' + $ts + '}'
}

# run another agent script and return its stdout as one string
function Run-Ps($file, $argv) {
  $out = & powershell.exe -NoProfile -NonInteractive -ExecutionPolicy Bypass -File "$DIR\$file" @argv 2>&1
  return (($out | ForEach-Object { [string]$_ }) -join "`n")
}
function Between($text, $mark) {
  if ($text -match ("(?s)<<<" + $mark + ">>>\s*(.*?)\s*<<<END>>>")) { return ($matches[1] -replace "[\r\n]+", "") }
  return $null
}
function Act($ok, $msg) { return '{"ok":' + (JBool $ok) + ',"msg":' + (JStr $msg) + '}' }

function Wait-Svc($state, $sec) {
  for ($i = 0; $i -lt $sec; $i++) { if ((Get-Service RvControlSvc).Status -eq $state) { return $true }; Start-Sleep 1 }
  return $false
}

function Dispatch($method, $path, $q) {
  switch ($path) {
    "/ping"  { return '{"ok":true,"version":' + (JStr $VERSION) + '}' }
    "/state" { return (Get-StateJson) }
    "/health" {
      $a = @(); if ($q["heal"] -eq "1") { $a = @("-Heal") }
      $j = Between (Run-Ps "health.ps1" $a) "HEALTH"
      if ($j) { return $j } else { return '{"error":"no health report","all_ok":false,"checks":[]}' }
    }
    "/update" {
      $a = @("-CheckOnly"); if ($q["install"] -eq "1") { $a = @() }
      $j = Between (Run-Ps "radmin-update.ps1" $a) "UPD"
      if ($j) { return $j } else { return '{"error":"no updater report"}' }
    }
  }
  if ($method -ne "POST") { return $null }
  switch ($path) {
    "/connect" {
      Set-ItemProperty $BASE -Name PowerOn -Value 1 -Type DWord
      Start-Service RvControlSvc
      return (Act (Wait-Svc "Running" 20) "connect")
    }
    "/disconnect" {
      Set-ItemProperty $BASE -Name PowerOn -Value 0 -Type DWord
      Stop-Service RvControlSvc -Force
      return (Act (Wait-Svc "Stopped" 20) "disconnect")
    }
    "/rename" {
      $n = ([string]$q["name"]).Replace('"','').Replace("'","").Trim()
      if ($n.Length -gt 63) { $n = $n.Substring(0, 63) }
      if (-not $n) { return (Act $false "empty name") }
      Set-ItemProperty $BASE -Name Alias -Value $n -Type String
      Restart-Service RvControlSvc -Force
      return (Act ((Get-ItemProperty $BASE).Alias -eq $n) "rename")
    }
    "/leave" {
      $g = [string]$q["guid"]
      if ($g -notmatch '^\{[0-9A-Fa-f-]{36}\}$') { return (Act $false "bad guid") }
      $k = "$BASE\Networks\$g"
      if (-not (Test-Path $k)) { return (Act $false "not a member") }
      Remove-Item $k -Recurse -Force
      Restart-Service RvControlSvc -Force
      return (Act $true "left $g")
    }
    "/reset-identity" { $o = Run-Ps "identity.ps1" @("-Reset"); return (Act ($o -match "ACTOK") "reset") }
    "/show-gui"       { & schtasks.exe /run /tn "RadminAgent-ShowGui" 2>&1 | Out-Null; return (Act $true "show") }
    "/orchestrate"    { $o = Run-Ps "net-orchestrator.ps1" @(); return (Act ($o -match "connectivity ready") "orchestrate") }
    "/ack-identity"   { Remove-Item "$DIR\identity-reset.txt" -Force; return (Act $true "ack") }
    "/dump" {
      $p = (Get-Process RvRvpnGui | Select-Object -First 1).Id
      if (-not $p) { return (Act $false "Radmin window not running") }
      Remove-Item "$DIR\gui.dmp" -Force
      & rundll32.exe C:\Windows\System32\comsvcs.dll, MiniDump $p "$DIR\gui.dmp" full
      for ($i = 0; $i -lt 20; $i++) { if (Test-Path "$DIR\gui.dmp") { break }; Start-Sleep 1 }
      Start-Sleep 2
      return (Act (Test-Path "$DIR\gui.dmp") "dump")
    }
  }
  return $null
}

function Parse-Query($qs) {
  $h = @{}
  if (-not $qs) { return $h }
  foreach ($kv in $qs.Split("&")) {
    $i = $kv.IndexOf("=")
    if ($i -lt 0) { $h[[Uri]::UnescapeDataString($kv)] = ""; continue }
    $k = [Uri]::UnescapeDataString($kv.Substring(0, $i).Replace("+", " "))
    $h[$k] = [Uri]::UnescapeDataString($kv.Substring($i + 1).Replace("+", " "))
  }
  return $h
}

function Send($stream, $code, $body) {
  $reason = @{200="OK";404="Not Found";500="Error"}[$code]
  $bytes = [Text.Encoding]::UTF8.GetBytes($body)
  $hdr = "HTTP/1.1 $code $reason`r`nContent-Type: application/json`r`nContent-Length: $($bytes.Length)`r`nConnection: close`r`n`r`n"
  $hb = [Text.Encoding]::ASCII.GetBytes($hdr)
  $stream.Write($hb, 0, $hb.Length)
  $stream.Write($bytes, 0, $bytes.Length)
  $stream.Flush()
}

Log "api $VERSION starting on port $Port"
$served = 0
while ($true) {
  $listener = $null
  try {
    $listener = New-Object System.Net.Sockets.TcpListener([System.Net.IPAddress]::Any, $Port)
    $listener.Start()
    while ($true) {
      $client = $listener.AcceptTcpClient()
      try {
        $remote = $client.Client.RemoteEndPoint.Address.ToString()
        if (-not ($remote.StartsWith("192.168.137.") -or $remote -eq "127.0.0.1")) { continue }
        $client.ReceiveTimeout = 5000; $client.SendTimeout = 10000
        $stream = $client.GetStream()
        $reader = New-Object System.IO.StreamReader($stream, [Text.Encoding]::ASCII)
        $first = $reader.ReadLine()
        if (-not $first) { continue }
        while ($true) { $h = $reader.ReadLine(); if ($null -eq $h -or $h -eq "") { break } }
        $parts = $first.Split(" ")
        $method = $parts[0].ToUpper(); $target = $parts[1]
        $path = $target; $qs = ""
        $qi = $target.IndexOf("?")
        if ($qi -ge 0) { $path = $target.Substring(0, $qi); $qs = $target.Substring($qi + 1) }
        $body = $null
        try { $body = Dispatch $method $path (Parse-Query $qs) }
        catch { Send $stream 500 ('{"ok":false,"msg":' + (JStr $_.Exception.Message) + '}'); continue }
        if ($null -eq $body) { Send $stream 404 '{"ok":false,"msg":"unknown endpoint"}' }
        else { Send $stream 200 $body }
      } catch {
      } finally {
        $client.Close()
        $served++
        if ($served % 50 -eq 0) { [GC]::Collect() }
      }
    }
  } catch {
    Log ("listener error: " + $_.Exception.Message)
    if ($listener) { try { $listener.Stop() } catch {} }
    Start-Sleep 3
  }
}
