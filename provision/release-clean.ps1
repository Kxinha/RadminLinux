# ============================================================
#  release-clean.ps1 - run INSIDE a throwaway copy of the VM (offline) right
#  before it is frozen into a release image, in two boots:
#
#   -Stage 1  Radmin identity (node id, 26.x address, server, networks, chat,
#             aliases, the adapter's static IP), browser, logs, caches, setup
#             logs, power reports, WMI leftovers; neutral computer name; the
#             registry hives that held them are saved compact (no deleted
#             data inside) and swapped in at the next boot.
#   -Stage 2  (after the reboot, with the new computer name) clears what that
#             boot wrote, overwrites the NTFS journal and free MFT records,
#             marks the image fresh and zeroes the free space.
#  The build checks the frozen image for the old strings afterwards.
#  PowerShell 2.0. Prints <<<CLEANOK>>> at the end.
# ============================================================
param([int]$Stage = 1, [switch]$NoZero)
$ErrorActionPreference = "SilentlyContinue"
$WarningPreference = "SilentlyContinue"
$out = @()
function Say($m) { $script:out += $m }
$b = "HKLM:\SOFTWARE\Wow6432Node\Famatech\RadminVPN\1.0"

# nothing else may touch Radmin meanwhile (the boot task runs after an agent install)
& schtasks.exe /end /tn "RadminAgent-Boot" 2>&1 | Out-Null
& schtasks.exe /end /tn "RadminAgent-Health" 2>&1 | Out-Null
Get-WmiObject Win32_Process -Filter "Name='powershell.exe'" | Where-Object { $_.CommandLine -match "boot\.ps1|net-orchestrator|health\.ps1" } | ForEach-Object { [void]$_.Terminate() }

function Clear-Logs {
  Remove-Item C:\radmin-agent\*.log, C:\radmin-agent\*.dmp, C:\radmin-agent\*.txt, C:\radmin-agent\*.xml -Force
  Remove-Item "C:\Program Files (x86)\Radmin VPN\CHATLOGS\*" -Recurse -Force
  Remove-Item "C:\ProgramData\Famatech\Radmin VPN\*.log" -Force
  Remove-Item "C:\ProgramData\Microsoft\Windows\Power Efficiency Diagnostics\*" -Force
  Remove-Item C:\Windows\Temp\* -Recurse -Force
  Remove-Item C:\Windows\Prefetch\* -Force
  Remove-Item C:\Windows\System32\wdi\LogFiles\* -Recurse -Force
  Get-ChildItem C:\Windows -Force | Where-Object { $_.Name -match '^__\d' } | Remove-Item -Force
  foreach ($u in Get-ChildItem C:\Users | Where-Object { $_.PSIsContainer }) { Remove-Item (Join-Path $u.FullName "AppData\Local\Temp\*") -Recurse -Force }
  foreach ($l in (& wevtutil.exe el)) { & wevtutil.exe cl "$l" 2>&1 | Out-Null }
}

if ($Stage -eq 2) {
  Get-Process RvRvpnGui | Stop-Process -Force
  Stop-Service RvControlSvc -Force
  foreach ($v in "RID","IPv4","IPv6","ServerAddress","FailServers") { Remove-ItemProperty $b -Name $v -Force }
  Set-ItemProperty $b -Name Alias -Value "RADMIN-LINUX" -Type String
  Remove-Item "HKLM:\SOFTWARE\RadminLinux" -Recurse -Force
  Remove-Item C:\rl-hives -Recurse -Force
  Get-ChildItem C:\Windows\System32\config\RegBack -Force | Remove-Item -Force
  Say ("computer name now: " + $env:COMPUTERNAME)
  Clear-Logs
  # shortcuts carry the PC name they were made on (link tracker block)
  $mid = New-Object byte[] 16
  $nb = [Text.Encoding]::ASCII.GetBytes("radmin-linux"); [Array]::Copy($nb, $mid, $nb.Length)
  $patched = 0
  foreach ($lp in (& cmd.exe /c "dir /s /b /a-d C:\*.lnk 2>nul")) {
    try { $by = [IO.File]::ReadAllBytes($lp) } catch { continue }
    $hit = $false
    for ($i = $by.Length - 96; $i -ge 0; $i--) {
      if ($by[$i] -eq 0x60 -and $by[$i+4] -eq 3 -and $by[$i+7] -eq 0xA0 -and $by[$i+1] -eq 0 -and $by[$i+5] -eq 0 -and $by[$i+6] -eq 0) {
        [Array]::Copy($mid, 0, $by, $i + 16, 16); $hit = $true; break
      }
    }
    if ($hit) { try { [IO.File]::WriteAllBytes($lp, $by); $patched++ } catch {} }
  }
  Say "shortcuts re-tagged: $patched"
  # overwrite the NTFS journal ($LogFile, 64 MB circular) and the free MFT
  # records (deleted small files keep their content there) with junk-free churn
  $z = New-Object byte[] 600
  New-Item -ItemType Directory C:\rl-churn -Force | Out-Null
  for ($i = 0; $i -lt 40000; $i++) { [IO.File]::WriteAllBytes("C:\rl-churn\$i", $z) }
  Remove-Item C:\rl-churn -Recurse -Force
  Say "journal and MFT churned"
  "fresh" | Out-File C:\radmin-agent\fresh-image.flag -Encoding ASCII
  if (-not $NoZero) {
    $f = "C:\zero.fill"
    $buf = New-Object byte[] (8MB)
    try {
      $s = [IO.File]::Open($f, "Create", "Write", "None")
      while ($true) { $s.Write($buf, 0, $buf.Length) }
    } catch {} finally { if ($s) { $s.Close() } }
    Remove-Item $f -Force
    Say "free space zeroed"
  }
  Say "<<<CLEANOK>>>"
  $out -join "`r`n"
  exit 0
}

# ---------------- stage 1 ----------------
# 1. hardening + identity
& powershell.exe -NoProfile -NonInteractive -ExecutionPolicy Bypass -File C:\radmin-agent\harden.ps1 | Out-Null
& powershell.exe -NoProfile -NonInteractive -ExecutionPolicy Bypass -File C:\radmin-agent\identity.ps1 -Reset | Out-Null
Get-Process RvRvpnGui | Stop-Process -Force
Stop-Service RvControlSvc -Force
$b = "HKLM:\SOFTWARE\Wow6432Node\Famatech\RadminVPN\1.0"
foreach ($v in "RID","IPv4","IPv6","ServerAddress","FailServers") { Remove-ItemProperty $b -Name $v -Force }
Set-ItemProperty $b -Name Alias -Value "RADMIN-LINUX" -Type String
Remove-Item "HKLM:\SOFTWARE\RadminLinux" -Recurse -Force
Say ("radmin values left: " + ((Get-Item $b).Property -join ","))

# 2. browser (Firefox was used to download Radmin; its profile is personal data)
foreach ($ff in "C:\Program Files\Mozilla Firefox","C:\Program Files (x86)\Mozilla Firefox") {
  if (Test-Path "$ff\uninstall\helper.exe") {
    Get-Process firefox | Stop-Process -Force
    $p = Start-Process "$ff\uninstall\helper.exe" -ArgumentList "/S" -PassThru -Wait
    Start-Sleep 5
    Remove-Item $ff -Recurse -Force
    Say "firefox removed"
  }
}
Get-Service MozillaMaintenance | ForEach-Object { & sc.exe delete MozillaMaintenance | Out-Null }
Remove-Item "C:\Program Files (x86)\Mozilla Maintenance Service" -Recurse -Force

# 3. per-user data
foreach ($u in Get-ChildItem C:\Users | Where-Object { $_.PSIsContainer }) {
  $h = $u.FullName
  foreach ($d in "AppData\Roaming\Mozilla","AppData\Local\Mozilla","AppData\Local\Temp\*",
                 "AppData\Local\Microsoft\Windows\Temporary Internet Files\*",
                 "AppData\Local\Microsoft\Windows\History\*","AppData\Roaming\Microsoft\Windows\Cookies\*",
                 "AppData\Roaming\Microsoft\Windows\Recent\*","AppData\Local\CrashDumps\*",
                 "AppData\Local\Microsoft\Windows\WER\*","Downloads\*","Desktop\*.lnk.bak",
                 "AppData\Local\Microsoft\Windows\Explorer\thumbcache_*") {
    Remove-Item (Join-Path $h $d) -Recurse -Force
  }
}

# 4. system leftovers
Remove-Item C:\radmin-agent\*.log, C:\radmin-agent\*.dmp, C:\radmin-agent\*.txt, C:\radmin-agent\*.xml -Force
Remove-Item C:\radmin-agent\dl -Recurse -Force
Remove-Item "C:\Program Files (x86)\Radmin VPN\CHATLOGS\*" -Recurse -Force
Remove-Item "C:\ProgramData\Famatech\Radmin VPN\*.log" -Force
Remove-Item "C:\ProgramData\Microsoft\Windows\WER\*" -Recurse -Force
Remove-Item C:\Windows\Temp\* -Recurse -Force
Remove-Item C:\Windows\Prefetch\* -Force
Remove-Item C:\Windows\Minidump\* -Force
Remove-Item C:\Windows\MEMORY.DMP -Force
Remove-Item C:\Windows\SoftwareDistribution\Download\* -Recurse -Force
Remove-Item C:\Windows\Logs\CBS\*.log -Force
Remove-Item C:\Windows\Panther\* -Recurse -Force
Remove-Item "C:\ProgramData\Microsoft\Windows\Power Efficiency Diagnostics\*" -Force
Remove-Item "C:\ProgramData\Famatech\Radmin VPN\*.txt" -Force
Remove-Item C:\Windows\System32\wdi\LogFiles\* -Recurse -Force
Remove-Item C:\Windows\debug\*.log -Force
Get-ChildItem C:\Windows -Force | Where-Object { $_.Name -match '^__\d' } | Remove-Item -Force   # wmiexec output leftovers
& vssadmin.exe delete shadows /all /quiet | Out-Null
foreach ($l in (& wevtutil.exe el)) { & wevtutil.exe cl "$l" 2>&1 | Out-Null }
Get-ChildItem C:\Windows\System32\config\RegBack -Force | Remove-Item -Force
Say "logs and caches cleared"

# Radmin's adapter (and any ghost adapters across all ControlSets) may keep the
# last 26.x address as a static IP: blank any 26.x IP across all ControlSets
foreach ($cs in "ControlSet001", "ControlSet002", "CurrentControlSet") {
  $ifaces = "HKLM:\SYSTEM\$cs\Services\Tcpip\Parameters\Interfaces"
  if (Test-Path $ifaces) {
    Get-ChildItem $ifaces | ForEach-Object {
      $p = Get-ItemProperty $_.PSPath
      if (($p.IPAddress -match "^26\.") -or ($p.DhcpIPAddress -match "^26\.")) {
        Set-ItemProperty $_.PSPath -Name IPAddress -Value ([string[]]@("0.0.0.0")) -Type MultiString
        Set-ItemProperty $_.PSPath -Name SubnetMask -Value ([string[]]@("0.0.0.0")) -Type MultiString
        Set-ItemProperty $_.PSPath -Name DefaultGateway -Value ([string[]]@()) -Type MultiString
        Remove-ItemProperty $_.PSPath -Name DhcpIPAddress -Force -EA SilentlyContinue
      }
    }
  }
}

foreach ($cv in "HKLM:\SOFTWARE\Microsoft\Windows NT\CurrentVersion","HKLM:\SOFTWARE\Wow6432Node\Microsoft\Windows NT\CurrentVersion") {
  Set-ItemProperty $cv -Name RegisteredOwner -Value "Radmin-Linux"
  Set-ItemProperty $cv -Name RegisteredOrganization -Value ""
}

# 5. neutral computer name (applies on the next boot)
$cn = "RADMIN-LINUX"
Set-ItemProperty "HKLM:\SYSTEM\CurrentControlSet\Control\ComputerName\ComputerName" -Name ComputerName -Value $cn
Set-ItemProperty "HKLM:\SYSTEM\CurrentControlSet\Services\Tcpip\Parameters" -Name "NV Hostname" -Value $cn
Set-ItemProperty "HKLM:\SYSTEM\CurrentControlSet\Services\Tcpip\Parameters" -Name Hostname -Value $cn
Say "computer name -> $cn"

# compact copies of the hives that held identity / network names; swapped in by
# smss at the next boot, before they are loaded (their .LOG files go too)
Add-Type -Namespace RL -Name K -MemberDefinition @'
[DllImport("kernel32.dll", SetLastError=true, CharSet=CharSet.Unicode)] public static extern bool MoveFileEx(string a, string b, int f);
[DllImport("kernel32.dll", SetLastError=true, CharSet=CharSet.Unicode, EntryPoint="MoveFileExW")] public static extern bool DeleteAtBoot(string a, IntPtr zero, int f);
[DllImport("advapi32.dll", CharSet=CharSet.Unicode)] public static extern int RegReplaceKey(IntPtr k, string sub, string newf, string oldf);
[DllImport("ntdll.dll")] public static extern int RtlAdjustPrivilege(int p, bool on, bool thread, out bool was);
'@
New-Item -ItemType Directory C:\rl-hives -Force | Out-Null
$swap = @(,@("HKLM\SOFTWARE", "C:\Windows\System32\config\SOFTWARE"))
$acct = Get-WmiObject Win32_UserAccount -Filter "LocalAccount=True AND Name='bench'"
if ($acct -and (Test-Path "Registry::HKEY_USERS\$($acct.SID)")) {
  $swap += ,@("HKU\$($acct.SID)", "C:\Users\bench\NTUSER.DAT")
  $swap += ,@("HKU\$($acct.SID)_Classes", "C:\Users\bench\AppData\Local\Microsoft\Windows\UsrClass.dat")
}
$n = 0
foreach ($h in $swap) {
  $tmp = "C:\rl-hives\hive$n"; $n++
  & reg.exe save $h[0] $tmp /y 2>&1 | Out-Null
  if (Test-Path $tmp) {
    $ok = [RL.K]::MoveFileEx($tmp, $h[1], 5)          # REPLACE_EXISTING | DELAY_UNTIL_REBOOT
    foreach ($lg in ".LOG", ".LOG1", ".LOG2") { if (Test-Path ($h[1] + $lg)) { [void][RL.K]::DeleteAtBoot($h[1] + $lg, [IntPtr]::Zero, 4) } }
    Say ("hive " + $h[0] + " compact copy " + (Get-Item $tmp).Length + " bytes, swap scheduled: " + $ok)
  }
}
# SYSTEM is in use from the boot loader on, so smss cannot swap it: the kernel
# does it (RegReplaceKey, what registry compactors use). The old file lands in
# C:\rl-hives and is deleted by stage 2.
& reg.exe save HKLM\SYSTEM C:\rl-hives\system.new /y 2>&1 | Out-Null
if (Test-Path C:\rl-hives\system.new) {
  $len = (Get-Item C:\rl-hives\system.new).Length
  $was = $false
  [void][RL.K]::RtlAdjustPrivilege(17, $true, $false, [ref]$was)   # SeBackupPrivilege
  [void][RL.K]::RtlAdjustPrivilege(18, $true, $false, [ref]$was)   # SeRestorePrivilege
  $hklm = New-Object IntPtr ([int]-2147483646)                      # HKEY_LOCAL_MACHINE
  $rc = [RL.K]::RegReplaceKey($hklm, "SYSTEM", "C:\rl-hives\system.new", "C:\rl-hives\system.old")
  foreach ($lg in ".LOG", ".LOG1", ".LOG2") { $lp = "C:\Windows\System32\config\SYSTEM" + $lg; if (Test-Path $lp) { [void][RL.K]::DeleteAtBoot($lp, [IntPtr]::Zero, 4) } }
  Say ("hive HKLM\SYSTEM compact copy $len bytes, replace rc: $rc, old moved: " + (Test-Path C:\rl-hives\system.old))
}
Say "<<<CLEANOK>>>"
$out -join "`r`n"
