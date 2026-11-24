# ============================================================
#  logon-guard.ps1 - gets the "Windows Activation" dialog out of the way.
#
#  At every logon winlogon.exe runs slui.exe, and that one starts a second
#  slui.exe (-Embedding, through DCOM) that draws "Activate Windows now / The
#  activation period has expired". The first one holds the desktop (no
#  taskbar, Radmin's window and the agent's reader wait) for about 2 minutes
#  (measured: 118 s); the window itself stays until someone clicks it.
#  Registry switches (Manual, NotificationDisabled, the SPP notification
#  service) do not stop either one. This SYSTEM process closes both after 3 s,
#  which is what "Ask me later" does (measured: taskbar up 8 s after logon).
#  Only those two are touched: an slui.exe opened by hand (from explorer) is
#  left alone. Started by boot.ps1 at power-on, runs for 10 minutes at most.
# ============================================================
$ErrorActionPreference = "SilentlyContinue"
$DIR = "C:\radmin-agent"
function Log($m){ $ts=Get-Date -Format "yyyy-MM-dd HH:mm:ss"; Add-Content "$DIR\boot.log" "[$ts] logon-guard: $m" }
$mx = New-Object Threading.Mutex($false, "Global\RadminLinuxLogonGuard")
if (-not $mx.WaitOne(0)) { exit }
$first = @{}
$t0 = Get-Date
while (((Get-Date) - $t0).TotalSeconds -lt 600) {
  # Get-Process is cheap; WMI (parent, command line) only when slui is there
  if (Get-Process slui) {
    $list = @(Get-WmiObject Win32_Process -Filter "Name='slui.exe'")
    # the winlogon one first: it is what holds the desktop
    foreach ($p in ($list | Sort-Object { [int]("$($_.CommandLine)" -match "-Embedding") })) {
      $par = (Get-WmiObject Win32_Process -Filter ("ProcessId=" + $p.ParentProcessId)).Name
      if (-not ($par -eq "winlogon.exe" -or ($par -eq "svchost.exe" -and "$($p.CommandLine)" -match "-Embedding"))) { continue }
      $id = [int]$p.ProcessId
      if (-not $first.ContainsKey($id)) { $first[$id] = Get-Date; Log "activation dialog up (slui $id, from $par)"; continue }
      if (((Get-Date) - $first[$id]).TotalSeconds -ge 3) {
        [void]$p.Terminate()
        Log "activation dialog closed (slui $id)"
        $first[$id] = (Get-Date).AddYears(1)
      }
    }
  }
  Start-Sleep -Seconds 1
}
