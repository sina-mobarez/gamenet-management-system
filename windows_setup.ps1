# ==========================================
# Configuration Variables (Modify these)
# ==========================================
$ProjectDir = "C:\path\to\your\django_project"
# Use pythonw.exe to ensure the server runs entirely in the background with no console window
$PythonExe  = "C:\path\to\your\venv\Scripts\pythonw.exe" 
$Port       = "8000"
$TaskName   = "DjangoWebUI_Server"
$ShortcutName = "Django Web UI"

# ==========================================
# Pre-flight Check
# ==========================================
if (-NOT ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
    Write-Warning "Please run PowerShell as Administrator to configure Task Scheduler."
    Exit
}

Write-Host "Deploying $TaskName..." -ForegroundColor Cyan

# ==========================================
# 1. Task Scheduler Setup (Idempotent)
# ==========================================
# Remove existing task if it exists
$existingTask = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
if ($existingTask) {
    Write-Host "Removing existing scheduled task..."
    Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
}

# Create new task triggers and actions
$Action = New-ScheduledTaskAction -Execute $PythonExe -Argument "manage.py runserver 0.0.0.0:$Port" -WorkingDirectory $ProjectDir
$Trigger = New-ScheduledTaskTrigger -AtLogOn
$Principal = New-ScheduledTaskPrincipal -UserId "$env:USERDOMAIN\$env:USERNAME" -LogonType Interactive

# Register the task
Register-ScheduledTask -TaskName $TaskName -Action $Action -Trigger $Trigger -Principal $Principal -Force | Out-Null
Write-Host "Task Scheduler configured. Starting the service now..."

# Start it immediately so you don't have to reboot
Start-ScheduledTask -TaskName $TaskName

# ==========================================
# 2. Desktop Shortcut Setup (Idempotent)
# ==========================================
$DesktopPath = [Environment]::GetFolderPath("Desktop")
$ShortcutFile = Join-Path -Path $DesktopPath -ChildPath "$ShortcutName.url"

# A .url file is the cleanest way to open a browser tab natively in Windows
$UrlContent = @"
[InternetShortcut]
URL=http://localhost:$Port
"@

Set-Content -Path $ShortcutFile -Value $UrlContent -Force
Write-Host "Desktop shortcut created at $ShortcutFile."

Write-Host "Setup complete! The service is running in the background." -ForegroundColor Green