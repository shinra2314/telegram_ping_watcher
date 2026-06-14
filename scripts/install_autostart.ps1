<#
.SYNOPSIS
  Register Pulse Desk dashboard to launch automatically at user logon.

.DESCRIPTION
  Creates a Windows Task Scheduler task "PulseDashboard" that runs
  scripts\start_dashboard.ps1 hidden at logon, with restart-on-failure.
  Re-run to update. Remove with uninstall_autostart.ps1.

  Why Task Scheduler (not the Startup folder): it supports hidden execution,
  "restart on failure", and a logon trigger without a flashing console window.
#>
param(
    [string]$TaskName = "PulseDashboard"
)

$ErrorActionPreference = "Stop"
$ProjectDir = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
$StartScript = Join-Path $ProjectDir "scripts\start_dashboard.ps1"
$HiddenVbs = Join-Path $ProjectDir "scripts\start_hidden.vbs"

if (-not (Test-Path -LiteralPath $StartScript)) {
    throw "start_dashboard.ps1 not found at $StartScript"
}
if (-not (Test-Path -LiteralPath $HiddenVbs)) {
    throw "start_hidden.vbs not found at $HiddenVbs"
}

# Launch through wscript + the VBS so NO console window ever appears (a plain
# "powershell -WindowStyle Hidden" still flashes a console at logon).
$WScriptExe = "$env:WINDIR\System32\wscript.exe"
$LaunchArgs = "`"$HiddenVbs`""

function Install-StartupShortcut {
    # Per-user fallback: no admin needed. No restart-on-failure, but
    # start_dashboard.ps1 is idempotent and health-gated.
    $startup = [Environment]::GetFolderPath('Startup')
    $lnkPath = Join-Path $startup "$TaskName.lnk"
    $w = New-Object -ComObject WScript.Shell
    $s = $w.CreateShortcut($lnkPath)
    $s.TargetPath = $WScriptExe
    $s.Arguments = $LaunchArgs
    $s.WorkingDirectory = $ProjectDir
    $s.WindowStyle = 7
    $s.Description = "Pulse Desk Dashboard autostart"
    $s.Save()
    return $lnkPath
}

$registered = $false
try {
    $action = New-ScheduledTaskAction -Execute $WScriptExe -Argument $LaunchArgs -WorkingDirectory $ProjectDir
    $trigger = New-ScheduledTaskTrigger -AtLogOn
    $settings = New-ScheduledTaskSettingsSet `
        -AllowStartIfOnBatteries `
        -DontStopIfGoingOnBatteries `
        -StartWhenAvailable `
        -RestartCount 3 `
        -RestartInterval (New-TimeSpan -Minutes 1) `
        -ExecutionTimeLimit (New-TimeSpan -Hours 0)
    $principal = New-ScheduledTaskPrincipal -UserId "$env:USERDOMAIN\$env:USERNAME" -LogonType Interactive -RunLevel Limited
    Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger `
        -Settings $settings -Principal $principal -Force -ErrorAction Stop | Out-Null
    $registered = $true
} catch {
    Write-Warning "Task Scheduler registration failed ($($_.Exception.Message))."
    Write-Warning "Falling back to a per-user Startup shortcut (no admin required)."
}

if ($registered) {
    Write-Host "[install_autostart] Registered scheduled task '$TaskName' (runs at logon)." -ForegroundColor Green
    Write-Host "  Test now:    Start-ScheduledTask -TaskName $TaskName"
} else {
    $lnk = Install-StartupShortcut
    Write-Host "[install_autostart] Created Startup shortcut: $lnk" -ForegroundColor Green
    Write-Host "  Test now:    & `"$StartScript`""
}
Write-Host "  Remove:      .\scripts\uninstall_autostart.ps1"
