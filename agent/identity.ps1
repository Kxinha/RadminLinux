# ============================================================
#  identity.ps1 - gives every install its OWN Radmin VPN identity.
#
#  Radmin keeps the node identity in the registry (RID = node id, IPv4/IPv6 =
#  the 26.x address, ServerAddress) and the server re-sends the network list for
#  that RID. A VM image copied to many people therefore shares ONE node and
#  shows the networks of whoever built it. This script fixes that:
#
#    -Check  (boot)  reset only when this VM is a new copy:
#                      * the image ships C:\radmin-agent\fresh-image.flag, or
#                      * the QEMU SMBIOS UUID differs from the one stored here
#                    unless the host asked to keep it (SMBIOS OEM string
#                    "radmin-linux:keep-identity"), then the UUID is adopted.
#    -Reset          reset now (UI menu "Reset Radmin identity").
#
#  Reset = stop Radmin, delete RID/IPs/server/networks/chat/UI network state,
#  give a neutral alias, start Radmin -> it registers as a brand new node.
#  PowerShell 2.0 compatible. Log in C:\radmin-agent\identity.log
# ============================================================
param([switch]$Check, [switch]$Reset)
$ErrorActionPreference = "SilentlyContinue"
$LOG  = "C:\radmin-agent\identity.log"
$BASE = "HKLM:\SOFTWARE\Wow6432Node\Famatech\RadminVPN\1.0"
$MK   = "HKLM:\SOFTWARE\RadminLinux"
$FLAG = "C:\radmin-agent\fresh-image.flag"
$NOTE = "C:\radmin-agent\identity-reset.txt"
function Log($m){ $ts=Get-Date -Format "yyyy-MM-dd HH:mm:ss"; Add-Content $LOG "[$ts] $m"; Write-Output $m }

function Get-VmUuid {
  $u = (Get-WmiObject Win32_ComputerSystemProduct).UUID
  if (-not $u) { return "" }
  $u = ([string]$u).ToUpper()
  if ($u -match '^[0F-]+$') { return "" }   # all zeros / all F = no -uuid given
  return $u
}

function Get-OemStrings {
  $a = (Get-WmiObject Win32_ComputerSystem).OEMStringArray
  if ($a) { return @($a) } else { return @() }
}

function Wait-Svc($state, $sec) {
  for ($i = 0; $i -lt $sec; $i++) {
    if ((Get-Service RvControlSvc).Status -eq $state) { return $true }
    Start-Sleep 1
  }
  return $false
}

function Clear-UserNetworkState {
  # The GUI remembers network names per user (ui\networks). Clear it for every
  # profile, loading the hive of users that are not logged on.
  foreach ($p in Get-ChildItem "C:\Users" | Where-Object { $_.PSIsContainer }) {
    $dat = Join-Path $p.FullName "NTUSER.DAT"
    if (-not (Test-Path $dat)) { continue }
    $sid = $null
    $acct = Get-WmiObject Win32_UserAccount -Filter "LocalAccount=True" | Where-Object { $_.Name -eq $p.Name } | Select-Object -First 1
    if ($acct) { $sid = $acct.SID }
    $loaded = $false
    $root = $null
    if ($sid -and (Test-Path "Registry::HKEY_USERS\$sid")) {
      $root = "Registry::HKEY_USERS\$sid"
    } else {
      & reg.exe load "HKU\RLTMP" "$dat" 2>&1 | Out-Null
      if (Test-Path "Registry::HKEY_USERS\RLTMP") { $root = "Registry::HKEY_USERS\RLTMP"; $loaded = $true }
    }
    if ($root) {
      $k = "$root\Software\Famatech\Radmin VPN\ui\networks"
      if (Test-Path $k) { Get-ChildItem $k | ForEach-Object { Remove-Item $_.PSPath -Recurse -Force } }
      $j = "$root\Software\Famatech\Radmin VPN\ui\JoinWindow"
      if (Test-Path $j) { Remove-ItemProperty $j -Name Search -Force }
    }
    if ($loaded) { [gc]::Collect(); Start-Sleep 1; & reg.exe unload "HKU\RLTMP" 2>&1 | Out-Null }
  }
}

function Reset-Identity($why) {
  Log "reset identity ($why)"
  $gui = [bool](Get-Process RvRvpnGui)
  Get-Process RvRvpnGui | Stop-Process -Force
  Stop-Service RvControlSvc -Force
  [void](Wait-Svc "Stopped" 30)
  foreach ($v in "RID","IPv4","IPv6","ServerAddress","FailServers") {
    Remove-ItemProperty $BASE -Name $v -Force
  }
  foreach ($sub in "Networks","ChatRegionSupport") {
    $k = "$BASE\$sub"
    if (Test-Path $k) { Get-ChildItem $k | ForEach-Object { Remove-Item $_.PSPath -Recurse -Force } }
  }
  $hex = "{0:X4}" -f (Get-Random -Minimum 0 -Maximum 65535)
  Set-ItemProperty $BASE -Name Alias -Value ("Linux-" + $hex) -Type String
  Set-ItemProperty $BASE -Name PowerOn -Value 1 -Type DWord
  Clear-UserNetworkState
  Remove-Item "C:\Program Files (x86)\Radmin VPN\CHATLOGS\*" -Recurse -Force
  Remove-Item "C:\ProgramData\Famatech\Radmin VPN\service.log" -Force
  Start-Service RvControlSvc
  [void](Wait-Svc "Running" 30)
  # the GUI caches the old node; bring it back (the member list needs it)
  if ($gui) { & schtasks.exe /run /tn "RadminAgent-ShowGui" 2>&1 | Out-Null }
  (Get-Date -Format "yyyy-MM-dd HH:mm:ss") + " " + $why | Out-File $NOTE -Encoding ASCII
  Log "identity reset done; Radmin registers as a new node (alias Linux-$hex)"
}

function Save-Uuid($uuid) {
  if (-not (Test-Path $MK)) { New-Item -Path $MK -Force | Out-Null }
  Set-ItemProperty $MK -Name InstanceUUID -Value $uuid -Type String
}

if ($Reset) {
  Reset-Identity "requested by the user"
  $u = Get-VmUuid; if ($u) { Save-Uuid $u }
  Remove-Item $FLAG -Force
  Write-Output "<<<ACTOK>>>"
  exit 0
}

# -Check (default)
$uuid   = Get-VmUuid
$stored = (Get-ItemProperty $MK).InstanceUUID
$fresh  = Test-Path $FLAG
$keep   = (Get-OemStrings) -contains "radmin-linux:keep-identity"

if ($fresh) {
  Reset-Identity "first boot of a fresh image"
  if ($uuid) { Save-Uuid $uuid }
  Remove-Item $FLAG -Force
} elseif (-not $uuid) {
  Log "no SMBIOS UUID from the host (old launcher); identity left as is"
} elseif ($stored -eq $uuid) {
  # same install, nothing to do
} elseif ($keep) {
  Log "host asked to keep the identity; adopting UUID $uuid"
  Save-Uuid $uuid
} else {
  $why = "copied VM image"
  if (-not $stored) { $why = "image from an older release (shared identity)" }
  Reset-Identity $why
  Save-Uuid $uuid
}
Write-Output "<<<ACTOK>>>"
