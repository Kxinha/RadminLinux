' starts gui-reader.ps1 with no console window flashing on the VM screen
CreateObject("WScript.Shell").Run "powershell.exe -NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File C:\radmin-agent\gui-reader.ps1", 0, False
