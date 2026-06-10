# PowerShell script to create a clean shortcut for Dictate on the Desktop
# that runs pythonw.exe directly without flashing a terminal window.

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
if ([string]::IsNullOrEmpty($ScriptDir)) {
    $ScriptDir = Get-Location
}

$ShortcutPath = Join-Path ([Environment]::GetFolderPath('Desktop')) "Dictate.lnk"
$Target = Join-Path $ScriptDir ".venv\Scripts\pythonw.exe"
$Arguments = "-m src.main"
$IconPath = Join-Path $ScriptDir "icon.ico"

Write-Host "Creating shortcut at: $ShortcutPath"
Write-Host "Target: $Target"
Write-Host "Arguments: $Arguments"
Write-Host "Working Directory: $ScriptDir"

try {
    $WshShell = New-Object -ComObject WScript.Shell
    $Shortcut = $WshShell.CreateShortcut($ShortcutPath)
    $Shortcut.TargetPath = $Target
    $Shortcut.Arguments = $Arguments
    $Shortcut.WorkingDirectory = $ScriptDir
    $Shortcut.IconLocation = $IconPath
    $Shortcut.Save()
    
    Write-Host "`nSuccessfully created the Desktop shortcut!" -ForegroundColor Green
    Write-Host "It runs windowless (no flashing command prompt) and without UAC elevation." -ForegroundColor Green
} catch {
    Write-Warning "`nAn error occurred while creating the shortcut: $_"
}
