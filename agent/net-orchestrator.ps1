# ============================================================
#  net-orchestrator.ps1  -  ensures Radmin<->Linux connectivity
#  Runs at VM boot and on demand. Resolves the ordering deadlock:
#   1. clean state (remove ICS from everything)
#   2. wait for Radmin to actually connect (RvControlSvc Running + 26.x IP)
#   3. apply ICS: Radmin adapter = public, isolated adapter = private
#   4. check 192.168.137.1 on the isolated adapter
#  Idempotent. Log in C:\radmin-agent\net.log
# ============================================================
param([int]$WaitSec = 120)
$ErrorActionPreference = "SilentlyContinue"
$ISO_MAC = "525400260002"   # isolated adapter (connected to Linux's tapradmin)
$LOG = "C:\radmin-agent\net.log"

function Log($m){ $ts=Get-Date -Format "HH:mm:ss"; Add-Content $LOG "[$ts] $m"; Write-Output $m }

function RadminNic {
  Get-WmiObject Win32_NetworkAdapter | Where-Object { $_.NetConnectionID -and $_.Description -match "Radmin" } | Select-Object -First 1
}
function IsoNic {
  Get-WmiObject Win32_NetworkAdapter | Where-Object { $_.MACAddress -and $_.MACAddress.Replace(":","").ToUpper() -eq $ISO_MAC } | Select-Object -First 1
}
function RadminIp {
  $n = RadminNic
  if(-not $n){ return $null }
  $c = Get-WmiObject Win32_NetworkAdapterConfiguration -Filter "Index=$($n.Index)"
  ($c.IPAddress | Where-Object { $_ -like "26.*" } | Select-Object -First 1)
}

Log "=== orchestrator starting ==="

# 1. wait for Radmin to connect (service + mesh IP)
$deadline = (Get-Date).AddSeconds($WaitSec)
$connected = $false
while((Get-Date) -lt $deadline){
  $svc = (Get-Service RvControlSvc).Status
  $ip  = RadminIp
  if($svc -eq "Running" -and $ip){ $connected = $true; Log "Radmin connected: $ip"; break }
  Start-Sleep 3
}
if(-not $connected){
  # force the service up and try again briefly
  Log "Radmin did not connect; starting service"
  Start-Service RvControlSvc
  Start-Sleep 8
  if(-not (RadminIp)){ Log "ERROR: no Radmin IP after waiting"; exit 1 }
}

$rad = RadminNic
$iso = IsoNic
if(-not $rad){ Log "ERROR: Radmin adapter not found"; exit 1 }
if(-not $iso){ Log "ERROR: isolated adapter (MAC $ISO_MAC) not found"; exit 1 }
Log "adapters: radmin='$($rad.NetConnectionID)' isolated='$($iso.NetConnectionID)'"

# 2. current ICS state
$share = New-Object -ComObject HNetCfg.HNetShare
function CfgFor($name){
  foreach($c in $share.EnumEveryConnection){
    if($share.NetConnectionProps($c).Name -eq $name){ return $share.INetSharingConfigurationForINetConnection($c) }
  }
  return $null
}
$radCfg = CfgFor $rad.NetConnectionID
$isoCfg = CfgFor $iso.NetConnectionID

$radPub = ($radCfg.SharingEnabled -and $radCfg.SharingConnectionType -eq 0)
$isoPriv = ($isoCfg.SharingEnabled -and $isoCfg.SharingConnectionType -eq 1)

if($radPub -and $isoPriv){
  Log "ICS already correct (Radmin=public, isolated=private)"
} else {
  Log "reapplying ICS"
  # clear 192.168.137.1 from any ghost interfaces in Tcpip\Parameters\Interfaces (prevents 0x80040201)
  Get-ChildItem "HKLM:\SYSTEM\CurrentControlSet\services\Tcpip\Parameters\Interfaces" -EA SilentlyContinue | ForEach-Object {
    $p = Get-ItemProperty $_.PSPath -EA SilentlyContinue
    if ($_.PSChildName -ne $iso.GUID -and ($p.IPAddress -contains "192.168.137.1")) {
      Log "clearing ghost 192.168.137.1 from $($_.PSChildName)"
      Set-ItemProperty $_.PSPath -Name EnableDHCP -Value 1 -Type DWord -EA SilentlyContinue
      Set-ItemProperty $_.PSPath -Name IPAddress -Value @("0.0.0.0") -Type MultiString -EA SilentlyContinue
      Set-ItemProperty $_.PSPath -Name SubnetMask -Value @("0.0.0.0") -Type MultiString -EA SilentlyContinue
    }
  }
  # delete any ghost/unplugged HNet_ConnectionProperties & HNet_Connection in root\Microsoft\HomeNet (prevents 0x80040201)
  $activeGuids = @(Get-WmiObject Win32_NetworkAdapter | Where-Object { $_.NetConnectionID -and $_.GUID } | ForEach-Object { $_.GUID })
  Get-WmiObject -Namespace root\Microsoft\HomeNet -Class HNet_ConnectionProperties -EA SilentlyContinue | ForEach-Object {
    $g = if ($_.Connection -match '(\{[0-9A-Fa-f\-]+\})') { $matches[1] } else { "" }
    if ($g -and ($activeGuids -notcontains $g)) { try { $_.Delete() } catch {} }
  }
  Get-WmiObject -Namespace root\Microsoft\HomeNet -Class HNet_Connection -EA SilentlyContinue | ForEach-Object {
    if ($_.Guid -and ($activeGuids -notcontains $_.Guid)) { try { $_.Delete() } catch {} }
  }
  foreach($c in $share.EnumEveryConnection){
    $cfg=$share.INetSharingConfigurationForINetConnection($c)
    if($cfg.SharingEnabled){ $cfg.DisableSharing() }
  }
  Start-Sleep 2
  (CfgFor $rad.NetConnectionID).EnableSharing(0)   # public
  Start-Sleep 2
  (CfgFor $iso.NetConnectionID).EnableSharing(1)   # private
  Start-Sleep 3
  Log "ICS applied"
}

# 3. confirm the isolated adapter IP (ICS forces 192.168.137.1; enforce static fallback if DHCP/APIPA lingered)
$isoCfgW = Get-WmiObject Win32_NetworkAdapterConfiguration -Filter "Index=$($iso.Index)"
$isoIp = $isoCfgW.IPAddress | Where-Object { $_ -like "192.168.137.*" }
if(-not $isoIp){
  Log "isolated adapter missing 192.168.137.1; setting static 192.168.137.1/24"
  netsh interface ip set address name="$($iso.NetConnectionID)" static 192.168.137.1 255.255.255.0 | Out-Null
  Start-Sleep 2
  $isoCfgW = Get-WmiObject Win32_NetworkAdapterConfiguration -Filter "Index=$($iso.Index)"
  $isoIp = $isoCfgW.IPAddress | Where-Object { $_ -like "192.168.137.*" }
}
Log "isolated adapter IP: $isoIp"
if($isoIp){ Log "=== OK: connectivity ready ===" } else { Log "WARN: isolated adapter has no 192.168.137.x IP" }
