# ============================================================
#  radmin-update.ps1 - finds the latest Radmin VPN version
#  straight from the official page, compares it with the installed one and installs.
#  -CheckOnly : only reports (JSON), does not install.
#  JSON output between <<<UPD>>> and <<<END>>>.
# ============================================================
param([switch]$CheckOnly)
$ErrorActionPreference = "SilentlyContinue"
$LOG = "C:\radmin-agent\update.log"
$PAGE = "https://www.radmin-vpn.com/"
function Log($m){ $ts=Get-Date -Format "HH:mm:ss"; Add-Content $LOG "[$ts] $m" }

function InstalledVer {
  $exe = "C:\Program Files (x86)\Radmin VPN\RvRvpnGui.exe"
  if(Test-Path $exe){ return (Get-Item $exe).VersionInfo.ProductVersion }
  return $null
}

# find the latest version + URL by scraping the official page
function DiscoverLatest {
  [System.Net.ServicePointManager]::SecurityProtocol = [System.Net.SecurityProtocolType]::Tls12 -bor [System.Net.SecurityProtocolType]::Tls
  try {
    $html = $null
    for($try = 1; $try -le 3 -and -not $html; $try++){
      try { $html = (New-Object System.Net.WebClient).DownloadString($PAGE) } catch { if($try -eq 3){ throw }; Start-Sleep 5 }
    }
  } catch { Log "failed to download page: $($_.Exception.Message)"; return $null }
  $mm = [regex]::Matches($html, "Radmin_VPN_([\d\.]+)\.exe")
  if($mm.Count -eq 0){ Log "no version on the page"; return $null }
  # take the HIGHEST version found
  $best = $null
  foreach($x in $mm){
    try { $v=[version]$x.Groups[1].Value } catch { continue }
    if(-not $best -or $v -gt $best){ $best = $v }
  }
  $verStr = $best.ToString()
  $url = [regex]::Match($html, "https?://[^""']*Radmin_VPN_" + [regex]::Escape($verStr) + "\.exe")
  $u = if($url.Success){ $url.Value } else { "https://download.radmin-vpn.com/download/files/Radmin_VPN_$verStr.exe" }
  return @{ ver = $verStr; url = $u }
}

$cur = InstalledVer
$latest = DiscoverLatest
$lat = if($latest){ $latest.ver } else { $null }
$url = if($latest){ $latest.url } else { $null }

$hasNew = $false
if($cur -and $lat){
  try { $hasNew = ([version]$lat -gt [version]$cur) } catch { $hasNew = ($cur -ne $lat) }
}
Log "installed=$cur latest=$lat new=$hasNew"

# Radmin 2.1+ installers need SHA-2 code signing support, which Windows 7 only
# has with update KB4474419. Without it the installer aborts, so say why.
$blocked = ""
if($hasNew){
  $os = Get-WmiObject Win32_OperatingSystem
  $isWin7 = ([string]$os.Version).StartsWith("6.1")
  $sha2 = [bool](Get-WmiObject Win32_QuickFixEngineering -Filter "HotFixID='KB4474419'")
  try { $needs = ([version]$lat -ge [version]"2.1") } catch { $needs = $false }
  if($isWin7 -and $needs -and -not $sha2){
    $blocked = "Radmin $lat needs the Windows 7 SHA-2 update (KB4474419), which this VM does not have. Radmin $cur keeps working."
    Log $blocked
  }
}

$installed = $false
$err = ""
if($hasNew -and -not $CheckOnly -and $url -and -not $blocked){
  $tmp = "C:\radmin-agent\Radmin_update.exe"
  try {
    Log "downloading $url"
    for($try = 1; $try -le 3; $try++){
      try { (New-Object System.Net.WebClient).DownloadFile($url, $tmp); break }
      catch { Log "download try $try failed: $($_.Exception.Message)"; if($try -eq 3){ throw }; Start-Sleep 5 }
    }
    $sig = Get-AuthenticodeSignature $tmp
    $signer = ""
    if($sig.SignerCertificate){ $signer = $sig.SignerCertificate.Subject }
    Log "signature: $($sig.Status) $signer"
    if($signer -notmatch "Famatech"){
      $err = "installer is not signed by Famatech ($($sig.Status)); not installing"
      Log $err
      Remove-Item $tmp -Force
    }
    if((Test-Path $tmp) -and -not $err){
      Log "installing silently"
      # Radmin's installer is Inno Setup: /S is ignored, it needs /VERYSILENT
      $p = Start-Process $tmp -ArgumentList "/VERYSILENT","/SUPPRESSMSGBOXES","/NORESTART" -Wait -PassThru
      $installed = ($p.ExitCode -eq 0)
      Remove-Item $tmp -Force
      Log "installed=$installed exit=$($p.ExitCode)"
    }
  } catch { $err = $_.Exception.Message; Log "install error: $err" }
}

function J($s){ if($null -eq $s){'null'}else{'"'+(([string]$s).Replace('\','\\').Replace('"','\"'))+'"'} }
Write-Output "<<<UPD>>>"
Write-Output ('{"installed_version":' + (J $cur) + ',"latest_version":' + (J $lat) + ',"download_url":' + (J $url) + ',"has_update":' + $hasNew.ToString().ToLower() + ',"did_install":' + $installed.ToString().ToLower() + ',"blocked":' + (J $blocked) + ',"error":' + (J $err) + '}')
Write-Output "<<<END>>>"
