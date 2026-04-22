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
# 1. Task Scheduler Setup (Windows 7 compatible via schtasks.exe)
# ==========================================
# Remove existing task if it exists (suppress errors)
Write-Host "Removing any existing scheduled task..."
schtasks /delete /tn $TaskName /f 2>$null

# Build the full command line for the task
$TaskCommand = "`"$PythonExe`" manage.py runserver 0.0.0.0:$Port"

# Create the scheduled task using schtasks.exe
# /sc ONLOGON  - trigger at user logon
# /it          - run only when user is logged on (interactive task)
# /ru          - run as the current user
# /rl HIGHEST  - run with highest privileges (admin)
# /f           - force creation (overwrite if exists)
Write-Host "Creating scheduled task..."
$createArgs = @(
    "/create",
    "/tn", "`"$TaskName`"",
    "/tr", "`"$TaskCommand`"",
    "/sc", "ONLOGON",
    "/it",
    "/ru", "$env:USERDOMAIN\$env:USERNAME",
    "/rl", "HIGHEST",
    "/f"
)
$createResult = schtasks @createArgs 2>&1

if ($LASTEXITCODE -ne 0) {
    Write-Error "Failed to create scheduled task. Error: $createResult"
    Exit 1
}

Write-Host "Task Scheduler configured. Starting the service now..."

# Start the task immediately (run once now)
schtasks /run /tn $TaskName

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