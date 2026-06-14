<#
.SYNOPSIS
  Remove the Pulse Desk auto-start logon task created by install_autostart.ps1.
#>
param(
    [string]$TaskName = "PulseDashboard"
)

$ErrorActionPreference = "Stop"

$removed = $false

$task = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
if ($task) {
    try {
        Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false -ErrorAction Stop
        Write-Host "[uninstall_autostart] Removed scheduled task '$TaskName'." -ForegroundColor Green
        $removed = $true
    } catch {
        Write-Warning "Could not remove scheduled task: $($_.Exception.Message)"
    }
}

$lnkPath = Join-Path ([Environment]::GetFolderPath('Startup')) "$TaskName.lnk"
if (Test-Path -LiteralPath $lnkPath) {
    Remove-Item -LiteralPath $lnkPath -Force
    Write-Host "[uninstall_autostart] Removed Startup shortcut: $lnkPath" -ForegroundColor Green
    $removed = $true
}

if (-not $removed) {
    Write-Host "[uninstall_autostart] Nothing to remove (no task or shortcut named '$TaskName')." -ForegroundColor Yellow
}
