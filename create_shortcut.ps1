# create_shortcut.ps1
# Run this once from your project folder to create the desktop shortcut.
# In Git Bash:  powershell -ExecutionPolicy Bypass -File create_shortcut.ps1

$ProjectDir  = Split-Path -Parent (Resolve-Path $MyInvocation.MyCommand.Path)
$IconFile    = Join-Path $ProjectDir "mbe_icon.ico"
$PythonExe   = Join-Path $ProjectDir "venv\Scripts\python.exe"
$WatchdogPy  = Join-Path $ProjectDir "watchdog.py"
$ShortcutPath = [System.IO.Path]::Combine(
    [Environment]::GetFolderPath("Desktop"),
    "MBE_Control_2026.lnk"
)

$WshShell = New-Object -ComObject WScript.Shell
$Shortcut = $WshShell.CreateShortcut($ShortcutPath)

$Shortcut.TargetPath       = $PythonExe
$Shortcut.Arguments        = "`"$WatchdogPy`""
$Shortcut.WorkingDirectory = $ProjectDir
$Shortcut.IconLocation     = $IconFile
$Shortcut.Description      = "MBE Control 2026"
$Shortcut.WindowStyle      = 1   # 1 = normal window, 7 = minimized (hides the console)

$Shortcut.Save()

Write-Host ""
Write-Host "Shortcut created on the Desktop: MBE_Control_2026"
Write-Host "  Target  : $PythonExe"
Write-Host "  Script  : $WatchdogPy"
Write-Host "  Icon    : $IconFile"
Write-Host "  Workdir : $ProjectDir"
Write-Host ""
