# ============================================================
#  harden.ps1 - makes the VM safe and light. Idempotent, runs at every boot.
#
#  1. Firewall ON. Inbound is allowed only from the Linux host over the
#     isolated link (192.168.137.0/24 -> 192.168.137.1). Before this the
#     firewall was OFF and the VM's admin login (public in this repo) was
#     reachable by every peer of every Radmin network the VM joined.
#  2. Services nobody needs here are disabled (IIS, FTP, MSMQ, SNMP, simple
#     TCP, print spooler, indexing, superfetch, RDP...). Less RAM and CPU in a
#     1 GB / 1 vCPU guest = Radmin and the VNC window stop stuttering.
#  3. Background disk churn off: defrag, system restore, CEIP, WinSAT.
#     (defrag inside a qcow2 also makes the image file grow for nothing)
#  4. No "Set Network Location" popup on the screen.
#  PowerShell 2.0 compatible. Log in C:\radmin-agent\harden.log
# ============================================================
$ErrorActionPreference = "SilentlyContinue"
$LOG = "C:\radmin-agent\harden.log"
function Log($m){ $ts=Get-Date -Format "yyyy-MM-dd HH:mm:ss"; Add-Content $LOG "[$ts] $m"; Write-Output $m }

# ---- 1. firewall ------------------------------------------------------------
# MpsSvc must keep running (ICS depends on it). Default inbound = block,
# outbound = allow. Radmin's own rules (control service, icmpv4) and the ICS
# DHCP/DNS rules stay as Windows/Radmin created them.
Set-Service MpsSvc -StartupType Automatic
Start-Service MpsSvc
& netsh advfirewall set allprofiles state on | Out-Null
& netsh advfirewall set allprofiles firewallpolicy "blockinbound,allowoutbound" | Out-Null
& netsh advfirewall firewall delete rule name="RadminLinux-Host" | Out-Null
& netsh advfirewall firewall add rule name="RadminLinux-Host" dir=in action=allow profile=any remoteip=192.168.137.0/24 localip=192.168.137.1 | Out-Null
# peers may ping this node (people test a Radmin network with ping); nothing else gets in
& netsh advfirewall firewall delete rule name="RadminLinux-Ping" | Out-Null
& netsh advfirewall firewall add rule name="RadminLinux-Ping" dir=in action=allow profile=any "protocol=icmpv4:8,any" | Out-Null
# make sure nothing else exposes SMB/RPC/WMI/RDP to the mesh
foreach ($g in "File and Printer Sharing","Remote Desktop","Windows Management Instrumentation (WMI)","Remote Administration","Remote Service Management","Remote Event Log Management","Remote Scheduled Tasks Management","Remote Volume Management","Windows Remote Management","Network Discovery","World Wide Web Services (HTTP)","FTP Server","SNMP Service","Telnet Server","LPD Service","Windows Media Player Network Sharing Service") {
  & netsh advfirewall firewall set rule group="$g" new enable=no 2>&1 | Out-Null
}
Log "firewall on: inbound only from the Linux host (192.168.137.0/24)"

# ---- 2. services --------------------------------------------------------------
# KEEP (needed): RvControlSvc, SharedAccess(ICS)+Netman+RasMan+TapiSrv+SstpSvc+BFE
# +MpsSvc, Winmgmt, LanmanServer/Workstation (SMB for the agent), Schedule,
# Dhcp, Dnscache, NlaSvc, netprofm, nsi, lmhosts (NetBIOS peer names), Themes.
$off = @(
  "W3SVC","WAS","AppHostSvc","IISADMIN","ftpsvc","MsDepSvc","WMSVC",
  "MSMQ","MSMQTriggers","NetMsmqActivator","NetPipeActivator","NetTcpActivator","NetTcpPortSharing",
  "SNMP","SNMPTRAP","LPDSVC","simptcp","iprip","NfsClnt","TlntSvr",
  "CISVC","WSearch","SysMain","Spooler","Fax",
  "TermService","UmRdpService","SessionEnv",
  "Browser","CscService","TrkWks","WPDBusEnum","SSDPSRV","upnphost","WMPNetworkSvc",
  "HomeGroupListener","HomeGroupProvider","iphlpsvc","RemoteRegistry","WebClient",
  "Audiosrv","AudioEndpointBuilder","MMCSS",
  "PcaSvc","WdiSystemHost","WdiServiceHost","DPS","AeLookupSvc","WerSvc",
  "ShellHWDetection","CertPropSvc","SCardSvr","TabletInputService",
  "IKEEXT","PolicyAgent","wscsvc","wuauserv","BITS","KeyboardFilter"
)
$changed = @()
foreach ($n in $off) {
  $s = Get-WmiObject Win32_Service -Filter "Name='$n'"
  if (-not $s) { continue }
  if ($s.StartMode -ne "Disabled") { & sc.exe config $n start= disabled | Out-Null; $changed += $n }
  if ($s.State -eq "Running") { Stop-Service $n -Force -WarningAction SilentlyContinue }
}
if ($changed.Count) { Log ("services disabled: " + ($changed -join " ")) }

# ---- 3. background disk churn -------------------------------------------------
foreach ($t in "\Microsoft\Windows\Defrag\ScheduledDefrag",
               "\Microsoft\Windows\SystemRestore\SR",
               "\Microsoft\Windows\Maintenance\WinSAT",
               "\Microsoft\Windows\Application Experience\AitAgent",
               "\Microsoft\Windows\Application Experience\ProgramDataUpdater",
               "\Microsoft\Windows\Customer Experience Improvement Program\Consolidator",
               "\Microsoft\Windows\Customer Experience Improvement Program\KernelCeipTask",
               "\Microsoft\Windows\Customer Experience Improvement Program\UsbCeip",
               "\Microsoft\Windows\DiskDiagnostic\Microsoft-Windows-DiskDiagnosticDataCollector",
               "\Microsoft\Windows\Registry\RegIdleBackup",
               "\Microsoft\Windows\Power Efficiency Diagnostics\AnalyzeSystem") {
  & schtasks.exe /change /tn "$t" /disable 2>&1 | Out-Null
}
Disable-ComputerRestore -Drive "C:\" 2>&1 | Out-Null
& vssadmin.exe delete shadows /all /quiet 2>&1 | Out-Null

# ---- 4. no popups on the VNC screen ------------------------------------------------
New-Item -Path "HKLM:\SYSTEM\CurrentControlSet\Control\Network\NewNetworkWindowOff" -Force | Out-Null
# no "Windows Error Reporting" dialogs
$wer = "HKLM:\SOFTWARE\Microsoft\Windows\Windows Error Reporting"
Set-ItemProperty $wer -Name DontShowUI -Value 1 -Type DWord
Set-ItemProperty $wer -Name Disabled -Value 1 -Type DWord

Log "=== hardened ==="
Write-Output "<<<ACTOK>>>"
