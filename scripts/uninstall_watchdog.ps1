# uninstall_watchdog.ps1 - remove the PulseWatchdog + PulseAutoLock scheduled tasks.
foreach ($name in @('PulseWatchdog', 'PulseAutoLock')) {
    if (Get-ScheduledTask -TaskName $name -ErrorAction SilentlyContinue) {
        Unregister-ScheduledTask -TaskName $name -Confirm:$false
        Write-Host "$name removed."
    } else {
        Write-Host "$name not installed."
    }
}
