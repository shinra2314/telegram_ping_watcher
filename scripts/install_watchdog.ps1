# install_watchdog.ps1 - register the PulseWatchdog + PulseAutoLock scheduled tasks.
# PulseWatchdog: runs scripts\pulse_watchdog.ps1 every 5 minutes (health probe +
#   auto-restart of the app host process).
# PulseAutoLock: locks the workstation 2 minutes after logon - pairs with
#   auto-logon so an unattended reboot comes back up but the desktop stays locked.
#   Disable on return: Disable-ScheduledTask -TaskName PulseAutoLock
#
# Registered via the ScheduledTasks module ONLY (never schtasks.exe: the repo
# path contains Cyrillic + "!!", which cmd/OEM codepages mangle).
param([switch]$NoAutoLock)

$ErrorActionPreference = "Stop"
$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$ProjectDir = Split-Path -Parent $ScriptDir
$WatchdogScript = Join-Path $ScriptDir "pulse_watchdog.ps1"
if (-not (Test-Path -LiteralPath $WatchdogScript)) { throw "not found: $WatchdogScript" }

$user = "$env:USERDOMAIN\$env:USERNAME"
$psExe = Join-Path $env:WINDIR "System32\WindowsPowerShell\v1.0\powershell.exe"
$principal = New-ScheduledTaskPrincipal -UserId $user -LogonType Interactive -RunLevel Limited

# ---------------- PulseWatchdog ----------------
# PS 5.1: -AtLogOn triggers have no -RepetitionInterval parameter; copy the
# Repetition CIM sub-object from a throwaway -Once trigger. -RepetitionDuration
# is OMITTED on purpose: empty Duration = repeat indefinitely
# ([TimeSpan]::MaxValue breaks Register-ScheduledTask).
$logon = New-ScheduledTaskTrigger -AtLogOn -User $user
$logon.Delay = 'PT3M'   # > start_dashboard.ps1's 30s network wait + 120s health gate
$logon.Repetition = (New-ScheduledTaskTrigger -Once -At (Get-Date).Date `
    -RepetitionInterval (New-TimeSpan -Minutes 5)).Repetition

# Second trigger starts ticking now (no fresh logon needed); distinct Repetition object.
$once = New-ScheduledTaskTrigger -Once -At (Get-Date).AddMinutes(5) `
    -RepetitionInterval (New-TimeSpan -Minutes 5)

$action = New-ScheduledTaskAction -Execute $psExe `
    -Argument ('-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File "{0}"' -f $WatchdogScript) `
    -WorkingDirectory $ProjectDir

$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries -ExecutionTimeLimit (New-TimeSpan -Minutes 10) `
    -MultipleInstances IgnoreNew

Register-ScheduledTask -TaskName 'PulseWatchdog' -Action $action `
    -Trigger @($logon, $once) -Settings $settings -Principal $principal -Force | Out-Null
Write-Host "PulseWatchdog registered (every 5 min, indefinitely)."

# ---------------- PulseAutoLock ----------------
if (-not $NoAutoLock) {
    $lockTrigger = New-ScheduledTaskTrigger -AtLogOn -User $user
    $lockTrigger.Delay = 'PT2M'
    $lockAction = New-ScheduledTaskAction -Execute (Join-Path $env:WINDIR "System32\rundll32.exe") `
        -Argument "user32.dll,LockWorkStation"
    $lockSettings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries `
        -DontStopIfGoingOnBatteries -ExecutionTimeLimit (New-TimeSpan -Minutes 2)
    Register-ScheduledTask -TaskName 'PulseAutoLock' -Action $lockAction `
        -Trigger $lockTrigger -Settings $lockSettings -Principal $principal -Force | Out-Null
    Write-Host "PulseAutoLock registered (locks screen 2 min after logon)."
}

# ---------------- self-check ----------------
Write-Host ""
Write-Host "PulseWatchdog trigger repetition (expect Interval=PT5M, empty Duration on both):"
(Get-ScheduledTask -TaskName 'PulseWatchdog').Triggers | ForEach-Object {
    $_.Repetition | Format-List Interval, Duration
}
