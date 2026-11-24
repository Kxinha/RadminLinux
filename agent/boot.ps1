# ============================================================
#  boot.ps1 - everything the VM does at power-on, in order (task RadminAgent-Boot).
#   1. identity.ps1 -Check   new copy of the image -> brand new Radmin node
#   2. harden.ps1            firewall + light services (idempotent)
#   3. power-guard.ps1       never sleep / hibernate / reboot by itself
#   4. net-orchestrator.ps1  wait for Radmin, apply ICS to the Linux link
#  In parallel from the start: logon-guard.ps1 (closes the activation dialog
#  that otherwise holds the desktop for 2 minutes at every logon)
#  Log in C:\radmin-agent\boot.log
# ============================================================
$ErrorActionPreference = "SilentlyContinue"
$DIR = "C:\radmin-agent"
function Log($m){ $ts=Get-Date -Format "yyyy-MM-dd HH:mm:ss"; Add-Content "$DIR\boot.log" "[$ts] $m" }
function Step($f, $argv) {
  $t0 = Get-Date
  $o = & powershell.exe -NoProfile -NonInteractive -ExecutionPolicy Bypass -File "$DIR\$f" @argv 2>&1
  Log ("$f done in {0:N1}s" -f ((Get-Date) - $t0).TotalSeconds)
}
# keep the boot log small
if ((Get-Item "$DIR\boot.log").Length -gt 200KB) { Remove-Item "$DIR\boot.log" -Force }
Log "=== boot ==="
Start-Process powershell.exe -WindowStyle Hidden -ArgumentList "-NoProfile -NonInteractive -ExecutionPolicy Bypass -File $DIR\logon-guard.ps1"
Step "identity.ps1" @("-Check")
Step "harden.ps1" @()
Step "power-guard.ps1" @()
Step "net-orchestrator.ps1" @()
Log "=== boot done ==="
