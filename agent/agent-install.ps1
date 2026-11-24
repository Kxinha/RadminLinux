# ============================================================
#  agent-install.ps1 - registers the Radmin-Linux agent tasks in the VM.
#   RadminAgent-Boot     at power-on (SYSTEM): boot.ps1
#   RadminAgent-Api      at power-on (SYSTEM): api.ps1, no time limit, restarts if it dies
#   RadminAgent-Health   every 15 min (SYSTEM): health.ps1 -Heal
#   RadminAgent-ShowGui  on demand, in the desktop session: brings the Radmin window up
#   RadminAgent-GuiReader at logon, in the desktop session: gui-reader.ps1 reads
#                        the Radmin window (networks, members) for the API
#  Removes the v1 tasks (Power / Net / Net-Logon), now folded into boot.ps1.
# ============================================================
$ErrorActionPreference = "SilentlyContinue"
$dir = "C:\radmin-agent"
New-Item -ItemType Directory -Path $dir -Force | Out-Null
$ps = "powershell.exe"
$psArgs = "-NoProfile -NonInteractive -ExecutionPolicy Bypass -WindowStyle Hidden -File"

# stop a running API (upgrade): the new api.ps1 starts at the end
& schtasks.exe /end /tn "RadminAgent-Api" 2>&1 | Out-Null
Get-WmiObject Win32_Process -Filter "Name='powershell.exe'" | Where-Object { $_.CommandLine -match "api\.ps1|gui-reader\.ps1" } | ForEach-Object { [void]$_.Terminate() }

foreach ($t in "RadminAgent-Power","RadminAgent-Net","RadminAgent-Net-Logon") {
  & schtasks.exe /delete /tn $t /f 2>&1 | Out-Null
}

function TaskXml($cmd, $args_, $trigger, $limit, $restart) {
  $r = ""
  if ($restart) { $r = "<RestartOnFailure><Interval>PT1M</Interval><Count>999</Count></RestartOnFailure>" }
@"
<?xml version="1.0" encoding="UTF-16"?>
<Task version="1.2" xmlns="http://schemas.microsoft.com/windows/2004/02/mit/task">
  <Triggers>$trigger</Triggers>
  <Principals><Principal id="Author"><UserId>S-1-5-18</UserId><RunLevel>HighestAvailable</RunLevel></Principal></Principals>
  <Settings>
    <MultipleInstancesPolicy>IgnoreNew</MultipleInstancesPolicy>
    <DisallowStartIfOnBatteries>false</DisallowStartIfOnBatteries>
    <StopIfGoingOnBatteries>false</StopIfGoingOnBatteries>
    <StartWhenAvailable>true</StartWhenAvailable>
    <IdleSettings><StopOnIdleEnd>false</StopOnIdleEnd><RestartOnIdle>false</RestartOnIdle></IdleSettings>
    <Enabled>true</Enabled>
    <ExecutionTimeLimit>$limit</ExecutionTimeLimit>
    <Priority>5</Priority>
    $r
  </Settings>
  <Actions Context="Author"><Exec><Command>$cmd</Command><Arguments>$args_</Arguments></Exec></Actions>
</Task>
"@
}
function Register($name, $xml) {
  $f = "$dir\task-$name.xml"
  [IO.File]::WriteAllText($f, $xml, [Text.Encoding]::Unicode)
  & schtasks.exe /create /tn $name /xml $f /f 2>&1 | Out-Null
  Remove-Item $f -Force
}
$boot = "<BootTrigger><Enabled>true</Enabled></BootTrigger>"
$every = "<TimeTrigger><Repetition><Interval>PT15M</Interval><StopAtDurationEnd>false</StopAtDurationEnd></Repetition><StartBoundary>2000-01-01T00:05:00</StartBoundary><Enabled>true</Enabled></TimeTrigger>"
Register "RadminAgent-Boot"   (TaskXml $ps "$psArgs $dir\boot.ps1" $boot "PT1H" $false)
Register "RadminAgent-Api"    (TaskXml $ps "$psArgs $dir\api.ps1" $boot "PT0S" $true)
Register "RadminAgent-Health" (TaskXml $ps "$psArgs $dir\health.ps1 -Heal" $every "PT10M" $false)
# interactive task (runs in the desktop session of the autologon user)
& schtasks.exe /create /tn "RadminAgent-ShowGui" /tr "wscript.exe $dir\show-gui.vbs" /sc once /st 00:00 /sd 01/01/2000 /ru bench /rp bench /it /f 2>&1 | Out-Null

# long running reader in the desktop session (no time limit; the API restarts
# it when its output goes stale)
$reader = @"
<?xml version="1.0" encoding="UTF-16"?>
<Task version="1.2" xmlns="http://schemas.microsoft.com/windows/2004/02/mit/task">
  <Triggers><LogonTrigger><Enabled>true</Enabled><UserId>bench</UserId></LogonTrigger></Triggers>
  <Principals><Principal id="Author"><UserId>bench</UserId><LogonType>InteractiveToken</LogonType><RunLevel>LeastPrivilege</RunLevel></Principal></Principals>
  <Settings>
    <MultipleInstancesPolicy>IgnoreNew</MultipleInstancesPolicy>
    <DisallowStartIfOnBatteries>false</DisallowStartIfOnBatteries>
    <StopIfGoingOnBatteries>false</StopIfGoingOnBatteries>
    <StartWhenAvailable>true</StartWhenAvailable>
    <IdleSettings><StopOnIdleEnd>false</StopOnIdleEnd><RestartOnIdle>false</RestartOnIdle></IdleSettings>
    <Enabled>true</Enabled>
    <ExecutionTimeLimit>PT0S</ExecutionTimeLimit>
    <Priority>7</Priority>
  </Settings>
  <Actions Context="Author"><Exec><Command>wscript.exe</Command><Arguments>$dir\gui-reader.vbs</Arguments></Exec></Actions>
</Task>
"@
Register "RadminAgent-GuiReader" $reader

& schtasks.exe /run /tn "RadminAgent-Api" 2>&1 | Out-Null
& schtasks.exe /run /tn "RadminAgent-GuiReader" 2>&1 | Out-Null
Write-Output "<<<AGENTOK>>>"
& schtasks.exe /query /fo csv 2>&1 | Select-String "RadminAgent" | ForEach-Object { $_.Line }
