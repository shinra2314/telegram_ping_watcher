<#
.SYNOPSIS
  Boot the Pulse Desk dashboard stack (host process + supervised services).

.DESCRIPTION
  Single entry point for auto-start. Steps:
    1. Wait for the network to be reachable (bounded).
    2. Launch the Pulse Desk host (uvicorn) hidden, via pythonw when available.
    3. Health-gate: poll /api/health until the host answers (or timeout).
  The Pulse Desk lifespan then registers + autostarts the managed services
  (Discord bot, etc.) from config/services.json.

  At logon this is launched windowless through scripts\start_hidden.vbs.
  Every run is logged to logs\launcher.log so logon-time issues are debuggable
  even though no console is visible.

  Run manually to test:  .\scripts\start_dashboard.ps1
  Skip opening a window:  .\scripts\start_dashboard.ps1 -NoOpen
#>
param(
    [switch]$NoOpen,
    [int]$HealthTimeoutSec = 120,
    [int]$NetworkTimeoutSec = 30
)

$ErrorActionPreference = "Stop"
$ProjectDir = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
Set-Location -LiteralPath $ProjectDir

$LogDir = Join-Path $ProjectDir "logs"
if (-not (Test-Path -LiteralPath $LogDir)) { New-Item -ItemType Directory -Path $LogDir | Out-Null }
$LogFile = Join-Path $LogDir "launcher.log"

function Log($msg) {
    $line = "{0} [start_dashboard] {1}" -f (Get-Date -Format "yyyy-MM-dd HH:mm:ss"), $msg
    Write-Host $line -ForegroundColor Cyan
    try { Add-Content -LiteralPath $LogFile -Value $line -Encoding utf8 } catch { }
}

function Open-Dashboard($url) {
    $edge = Get-Command msedge -ErrorAction SilentlyContinue
    $chrome = Get-Command chrome -ErrorAction SilentlyContinue
    if ($edge) { Start-Process $edge.Source "--app=$url" }
    elseif ($chrome) { Start-Process $chrome.Source "--app=$url" }
    else { Start-Process $url }
}

function Test-Health($url) {
    try {
        $r = Invoke-WebRequest -Uri "$url/api/health" -TimeoutSec 3 -UseBasicParsing
        return ($r.StatusCode -eq 200)
    } catch { return $false }
}

# --- 1. read host/port (defaults match config.py) -------------------------
$AppHost = "127.0.0.1"
$Port = 8000
if (Test-Path -LiteralPath (Join-Path $ProjectDir ".env")) {
    foreach ($line in Get-Content -LiteralPath (Join-Path $ProjectDir ".env")) {
        if ($line -match '^\s*HOST\s*=\s*(.+?)\s*$') { $AppHost = $Matches[1].Trim('"').Trim() }
        if ($line -match '^\s*PORT\s*=\s*(\d+)\s*$') { $Port = [int]$Matches[1] }
    }
}
$BaseUrl = "http://${AppHost}:${Port}"
Log "=== launch (host=$BaseUrl, healthTimeout=${HealthTimeoutSec}s) ==="

# --- 2. wait for network --------------------------------------------------
Log "Waiting for network (max ${NetworkTimeoutSec}s)..."
$deadline = (Get-Date).AddSeconds($NetworkTimeoutSec)
while ((Get-Date) -lt $deadline) {
    if (Test-Connection -ComputerName "8.8.8.8" -Count 1 -Quiet -ErrorAction SilentlyContinue) { break }
    Start-Sleep -Seconds 2
}

# --- 3. already running? --------------------------------------------------
if (Test-Health $BaseUrl) {
    Log "Pulse Desk already running at $BaseUrl"
    if (-not $NoOpen) { Open-Dashboard $BaseUrl }
    return
}

# --- 4. pick interpreter (pythonw = no console) ---------------------------
$VenvPyW = Join-Path $ProjectDir ".venv\Scripts\pythonw.exe"
$VenvPy = Join-Path $ProjectDir ".venv\Scripts\python.exe"
if (Test-Path -LiteralPath $VenvPyW) { $Interpreter = $VenvPyW }
elseif (Test-Path -LiteralPath $VenvPy) { $Interpreter = $VenvPy }
else {
    $py = Get-Command pythonw -ErrorAction SilentlyContinue
    if (-not $py) { $py = Get-Command python -ErrorAction SilentlyContinue }
    if (-not $py) { Log "ERROR: no Python interpreter found. Run run_local.ps1 first."; exit 1 }
    $Interpreter = $py.Source
}

Log "Launching host: $Interpreter main.py"
# Redirect std streams to files. Under pythonw (no console) the process would
# otherwise have invalid std handles; giving it real ones prevents a whole class
# of "write to None stdout/stderr" startup crashes and leaves a host log.
$HostOut = Join-Path $LogDir "host_stdout.log"
$HostErr = Join-Path $LogDir "host_stderr.log"
$proc = Start-Process -FilePath $Interpreter -ArgumentList "main.py" -WorkingDirectory $ProjectDir `
    -WindowStyle Hidden -PassThru -RedirectStandardOutput $HostOut -RedirectStandardError $HostErr

# --- 5. health gate -------------------------------------------------------
Log "Waiting for $BaseUrl/api/health (max ${HealthTimeoutSec}s)..."
$deadline = (Get-Date).AddSeconds($HealthTimeoutSec)
$ready = $false
while ((Get-Date) -lt $deadline) {
    if (Test-Health $BaseUrl) { $ready = $true; break }
    if ($proc -and $proc.HasExited) {
        Log "ERROR: host process exited early (code=$($proc.ExitCode)). Check logs\app.log."
        exit 1
    }
    Start-Sleep -Seconds 2
}

if ($ready) {
    Log "Pulse Desk is up at $BaseUrl"
    if (-not $NoOpen) { Open-Dashboard $BaseUrl }
    exit 0
}

# Not answering yet, but the process is still alive — cold start can be slow
# (Telethon connect, DB init). Don't fail the task; open optimistically.
if ($proc -and -not $proc.HasExited) {
    Log "Host still starting after ${HealthTimeoutSec}s but process is alive (pid=$($proc.Id)); opening anyway."
    if (-not $NoOpen) { Open-Dashboard $BaseUrl }
    exit 0
}

Log "ERROR: host did not start. Check logs\app.log."
exit 1
