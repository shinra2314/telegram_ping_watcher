# restart_app.ps1 - reliable Pulse Desk restart on :8000.
# Starts python.exe via WMI (Win32_Process.Create): the process is not tied
# to the parent shell and survives it closing; window hidden; stdout/stderr
# go to logs\runtime.out.log (the app also writes logs\app.log itself).
param([int]$Port = 8000)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $MyInvocation.MyCommand.Path

# 1. Stop current listener on the port, if any
$conn = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue | Select-Object -First 1
if ($conn) {
    Write-Host "Stopping pid=$($conn.OwningProcess) on :$Port"
    Stop-Process -Id $conn.OwningProcess -Force -Confirm:$false
    Start-Sleep -Seconds 2
}

# 2. Detached + hidden start, stderr captured via cmd redirect
$py = Join-Path $root ".venv\Scripts\python.exe"
if (-not (Test-Path $py)) { throw "venv python not found: $py" }
$logDir = Join-Path $root "logs"
New-Item -ItemType Directory -Force -Path $logDir | Out-Null
$runtimeLog = Join-Path $logDir "runtime.out.log"
$cmdline = "cmd.exe /c `"`"$py`" main.py >> `"$runtimeLog`" 2>&1`""
$startup = New-CimInstance -ClassName Win32_ProcessStartup -ClientOnly -Property @{ ShowWindow = [uint16]0 }
$r = Invoke-CimMethod -ClassName Win32_Process -MethodName Create -Arguments @{
    CommandLine               = $cmdline
    CurrentDirectory          = $root
    ProcessStartupInformation = $startup
}
if ($r.ReturnValue -ne 0) { throw "Win32_Process.Create failed, code $($r.ReturnValue)" }

# 3. Health check, up to 40 seconds
$ok = $false
$deadline = (Get-Date).AddSeconds(40)
do {
    Start-Sleep -Seconds 2
    try { $ok = (Invoke-WebRequest "http://127.0.0.1:$Port/" -UseBasicParsing -TimeoutSec 5).StatusCode -eq 200 } catch { $ok = $false }
} until ($ok -or (Get-Date) -gt $deadline)

if ($ok) {
    Write-Host "OK: http://127.0.0.1:$Port up (launcher pid=$($r.ProcessId))"
} else {
    Write-Host "FAIL: no response on :$Port - check $runtimeLog and logs\app.log"
    exit 1
}
