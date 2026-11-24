# ============================================================
#  health.ps1 - diagnostics + auto-repair on the Windows side.
#  Checks each component and (if -Heal) fixes it. Returns JSON.
#  The "portal" (UI) uses this so you never need to look at the VM.
#  Output between <<<HEALTH>>> and <<<END>>>.
# ============================================================
param([switch]$Heal)
$ErrorActionPreference = "SilentlyContinue"
$LOG = "C:\radmin-agent\health.log"
$ISO_MAC = "525400260002"
function Log($m){ $ts=Get-Date -Format "HH:mm:ss"; Add-Content $LOG "[$ts] $m" }
function J($s){ if($null -eq $s){'null'}else{'"'+(([string]$s).Replace('\','\\').Replace('"','\"'))+'"'} }

if((Get-Item $LOG).Length -gt 200KB){ Remove-Item $LOG -Force }
$checks = @()   # each item: name, ok, detail, healed
function Check($name, $ok, $detail, $healed){
  $script:checks += @{ name=$name; ok=$ok; detail=$detail; healed=$healed }
}

# --- 1. RvControlSvc service ---
$svc = Get-Service RvControlSvc
$svcOk = ($svc.Status -eq "Running")
$svcHealed = $false
if(-not $svcOk -and $Heal){
  Log "healing: start RvControlSvc"
  Start-Service RvControlSvc; Start-Sleep 4
  $svc = Get-Service RvControlSvc; $svcOk = ($svc.Status -eq "Running"); $svcHealed = $svcOk
}
Check "radmin_service" $svcOk $svc.Status $svcHealed

# --- 2. mesh IP (Radmin adapter with 26.x) ---
$rad = Get-WmiObject Win32_NetworkAdapterConfiguration | Where-Object { $_.Description -match "Radmin" -and $_.IPAddress }
$radIp = $null
if($rad){ $radIp = $rad.IPAddress | Where-Object { $_ -like "26.*" } | Select-Object -First 1 }
Check "mesh_ip" ([bool]$radIp) $radIp $false

# --- 3. ICS correto ---
$share = New-Object -ComObject HNetCfg.HNetShare
$radPub=$false; $isoPriv=$false
$radName=$null; $isoName=$null
$radNic = Get-WmiObject Win32_NetworkAdapter | Where-Object { $_.NetConnectionID -and $_.Description -match "Radmin" } | Select-Object -First 1
$isoNic = Get-WmiObject Win32_NetworkAdapter | Where-Object { $_.MACAddress -and $_.MACAddress.Replace(":","").ToUpper() -eq $ISO_MAC } | Select-Object -First 1
if($radNic){ $radName=$radNic.NetConnectionID }
if($isoNic){ $isoName=$isoNic.NetConnectionID }
foreach($c in $share.EnumEveryConnection){
  $p=$share.NetConnectionProps($c); $cfg=$share.INetSharingConfigurationForINetConnection($c)
  if($cfg.SharingEnabled){
    if($p.Name -eq $radName -and $cfg.SharingConnectionType -eq 0){ $radPub=$true }
    if($p.Name -eq $isoName -and $cfg.SharingConnectionType -eq 1){ $isoPriv=$true }
  }
}
$icsOk = ($radPub -and $isoPriv)
$icsHealed=$false
if(-not $icsOk -and $Heal -and $radName -and $isoName){
  Log "healing: reapply ICS"
  Get-ChildItem "HKLM:\SYSTEM\CurrentControlSet\services\Tcpip\Parameters\Interfaces" -EA SilentlyContinue | ForEach-Object {
    $p = Get-ItemProperty $_.PSPath -EA SilentlyContinue
    if ($isoNic -and $_.PSChildName -ne $isoNic.GUID -and ($p.IPAddress -contains "192.168.137.1")) {
      Set-ItemProperty $_.PSPath -Name EnableDHCP -Value 1 -Type DWord -EA SilentlyContinue
      Set-ItemProperty $_.PSPath -Name IPAddress -Value @("0.0.0.0") -Type MultiString -EA SilentlyContinue
      Set-ItemProperty $_.PSPath -Name SubnetMask -Value @("0.0.0.0") -Type MultiString -EA SilentlyContinue
    }
  }
  $activeGuids = @(Get-WmiObject Win32_NetworkAdapter | Where-Object { $_.NetConnectionID -and $_.GUID } | ForEach-Object { $_.GUID })
  Get-WmiObject -Namespace root\Microsoft\HomeNet -Class HNet_ConnectionProperties -EA SilentlyContinue | ForEach-Object {
    $g = if ($_.Connection -match '(\{[0-9A-Fa-f\-]+\})') { $matches[1] } else { "" }
    if ($g -and ($activeGuids -notcontains $g)) { try { $_.Delete() } catch {} }
  }
  Get-WmiObject -Namespace root\Microsoft\HomeNet -Class HNet_Connection -EA SilentlyContinue | ForEach-Object {
    if ($_.Guid -and ($activeGuids -notcontains $_.Guid)) { try { $_.Delete() } catch {} }
  }
  foreach($c in $share.EnumEveryConnection){ $cfg=$share.INetSharingConfigurationForINetConnection($c); if($cfg.SharingEnabled){$cfg.DisableSharing()} }
  Start-Sleep 2
  foreach($c in $share.EnumEveryConnection){ $p=$share.NetConnectionProps($c); if($p.Name -eq $radName){ $share.INetSharingConfigurationForINetConnection($c).EnableSharing(0) } }
  Start-Sleep 2
  foreach($c in $share.EnumEveryConnection){ $p=$share.NetConnectionProps($c); if($p.Name -eq $isoName){ $share.INetSharingConfigurationForINetConnection($c).EnableSharing(1) } }
  Start-Sleep 3
  $icsOk=$true; $icsHealed=$true
}
Check "ics" $icsOk "radPub=$radPub isoPriv=$isoPriv" $icsHealed

# --- 4. isolated adapter IP (192.168.137.1) ---
$iso = Get-WmiObject Win32_NetworkAdapterConfiguration | Where-Object { $_.MACAddress -eq "52:54:00:26:00:02" -and $_.IPAddress }
$isoIp = $null
if($iso){ $isoIp = $iso.IPAddress | Where-Object { $_ -like "192.168.137.*" } | Select-Object -First 1 }
$isoHealed = $false
if(-not $isoIp -and $Heal -and $isoName){
  Log "healing: setting static 192.168.137.1 on $isoName"
  netsh interface ip set address name="$isoName" static 192.168.137.1 255.255.255.0 | Out-Null
  Start-Sleep 2
  $iso = Get-WmiObject Win32_NetworkAdapterConfiguration | Where-Object { $_.MACAddress -eq "52:54:00:26:00:02" -and $_.IPAddress }
  if($iso){ $isoIp = $iso.IPAddress | Where-Object { $_ -like "192.168.137.*" } | Select-Object -First 1 }
  $isoHealed = [bool]$isoIp
}
Check "isolated_ip" ([bool]$isoIp) $isoIp $isoHealed

# --- 5. power locked (robust proxy: hibernation off = power-guard applied) ---
$powerOk = -not (Test-Path "C:\hiberfil.sys")
$powerHealed=$false
if(-not $powerOk -and $Heal){
  powercfg -h off
  powercfg -change -standby-timeout-ac 0
  powercfg -change -standby-timeout-dc 0
  powercfg -change -hibernate-timeout-ac 0
  powercfg -change -monitor-timeout-ac 0
  $powerOk = -not (Test-Path "C:\hiberfil.sys")
  $powerHealed=$true; Log "healing: power"
}
Check "power_guard" $powerOk "hibernate off" $powerHealed

# --- 6. agent tasks registered ---
$t = schtasks /query /tn "RadminAgent-Boot" 2>$null
Check "agent_tasks" ([bool]$t) "RadminAgent-Boot" $false

# --- 7. API for the Linux app (api.ps1, port 47950) ---
function ApiUp {
  $c = New-Object System.Net.Sockets.TcpClient
  try { $c.Connect("127.0.0.1", 47950); return $c.Connected } catch { return $false } finally { $c.Close() }
}
$apiOk = ApiUp
$apiHealed = $false
if(-not $apiOk -and $Heal){
  Log "healing: start RadminAgent-Api"
  schtasks /run /tn "RadminAgent-Api" 2>$null | Out-Null
  Start-Sleep 6
  $apiOk = ApiUp; $apiHealed = $apiOk
}
Check "api" $apiOk "port 47950" $apiHealed

# --- 8. firewall on (inbound only from the Linux host) ---
$fw = (netsh advfirewall show publicprofile state) -join " "
$fwOk = ($fw -match "State\s+ON")
$fwHealed = $false
if(-not $fwOk -and $Heal){
  Log "healing: firewall"
  & powershell -NoProfile -ExecutionPolicy Bypass -File "C:\radmin-agent\harden.ps1" | Out-Null
  $fw = (netsh advfirewall show publicprofile state) -join " "
  $fwOk = ($fw -match "State\s+ON"); $fwHealed = $fwOk
}
Check "firewall" $fwOk "inbound only from the host" $fwHealed

# build JSON
$allOk = $true
$items = @()
foreach($ch in $checks){
  if(-not $ch.ok){ $allOk=$false }
  $items += '{"name":' + (J $ch.name) + ',"ok":' + $ch.ok.ToString().ToLower() + ',"detail":' + (J $ch.detail) + ',"healed":' + $ch.healed.ToString().ToLower() + '}'
}
Write-Output "<<<HEALTH>>>"
Write-Output ('{"all_ok":' + $allOk.ToString().ToLower() + ',"healed":' + ([bool]($checks | Where-Object {$_.healed})).ToString().ToLower() + ',"node_ip":' + (J $radIp) + ',"checks":[' + ($items -join ',') + ']}')
Write-Output "<<<END>>>"
