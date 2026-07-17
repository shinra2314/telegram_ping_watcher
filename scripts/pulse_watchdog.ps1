# pulse_watchdog.ps1 - external health watchdog for Pulse Desk.
# Run by Task Scheduler (PulseWatchdog) every 5 minutes. Probes /api/health;
# if the endpoint is unreachable across 3 probes, restarts the app via the
# known-good restart_app.ps1 (WMI-detached python.exe). Never restarts on
# HTTP 200 - "degraded"/stale jobs are handled by in-app supervision and
# Telegram alerts, not by killing the process.
#
# -DryRun        log decisions, never kill/start anything
# -PortOverride  probe a different port (lets tests exercise the failure
#                branch against a dead port without touching the real app)
param(
    [switch]$DryRun,
    [int]$PortOverride = 0
)

$ErrorActionPreference = "Continue"
$root = Split-Path -Parent $PSScriptRoot
$logDir = Join-Path $root "logs"
New-Item -ItemType Directory -Force -Path $logDir | Out-Null
$logFile = Join-Path $logDir "watchdog.log"
$stateFile = Join-Path $logDir "watchdog_state.txt"

# --- own log rotation (sole writer; MultipleInstances=IgnoreNew guarantees no lock) ---
if ((Test-Path -LiteralPath $logFile) -and ((Get-Item -LiteralPath $logFile).Length -gt 2MB)) {
    Remove-Item -LiteralPath "$logFile.1" -Force -ErrorAction SilentlyContinue
    Move-Item -LiteralPath $logFile -Destination "$logFile.1" -Force
}

function Write-Log([string]$msg) {
    $line = "{0} [watchdog] {1}" -f (Get-Date -Format "yyyy-MM-dd HH:mm:ss"), $msg
    Add-Content -LiteralPath $logFile -Value $line -Encoding UTF8
}

# --- resolve port: .env PORT= wins, fallback 8000, override for tests ---
$port = 8000
$envPath = Join-Path $root ".env"
if (Test-Path -LiteralPath $envPath) {
    foreach ($line in (Get-Content -LiteralPath $envPath)) {
        if ($line -match '^\s*PORT\s*=\s*(\d+)\s*$') { $port = [int]$Matches[1] }
    }
}
if ($PortOverride -gt 0) { $port = $PortOverride }
$healthUrl = "http://127.0.0.1:$port/api/health"

# --- probe loop: 3 attempts, 25 s apart; any 200 => healthy, done ---
$maxProbes = 3
$healthy = $false
for ($i = 1; $i -le $maxProbes; $i++) {
    try {
        $resp = Invoke-WebRequest -Uri $healthUrl -UseBasicParsing -TimeoutSec 5
        if ($resp.StatusCode -eq 200) {
            $j = $null
            try { $j = $resp.Content | ConvertFrom-Json } catch {}
            if ($j -and $j.shutting_down) {
                Write-Log "healthy but shutting_down=true, not interfering"
            } else {
                $status = "unknown"; if ($j -and $j.status) { $status = $j.status }
                Write-Log "healthy (status=$status)"
            }
            $healthy = $true
            break
        }
        Write-Log ("probe {0}/{1} FAIL: HTTP {2}" -f $i, $maxProbes, $resp.StatusCode)
    } catch {
        if ($_.Exception.Response) {
            $code = 0; try { $code = [int]$_.Exception.Response.StatusCode } catch {}
            Write-Log ("probe {0}/{1} FAIL: listener up but HTTP {2}" -f $i, $maxProbes, $code)
        } else {
            Write-Log ("probe {0}/{1} FAIL: unreachable ({2})" -f $i, $maxProbes, $_.Exception.Message)
        }
    }
    if ($i -lt $maxProbes) { Start-Sleep -Seconds 25 }
}
if ($healthy) { exit 0 }

# --- guard: OS just booted - the PulseDashboard logon task owns startup ---
$boot = (Get-CimInstance Win32_OperatingSystem).LastBootUpTime
$bootAge = ((Get-Date) - $boot).TotalSeconds
if ($bootAge -lt 300) {
    Write-Log ("guard: OS booted {0}s ago (<300s), leaving startup to logon launcher" -f [int]$bootAge)
    exit 0
}

# --- guard: app instance mid-startup (cold start takes 30-60 s) ---
# NOTE: repo path contains Cyrillic + "!!" - never put it inside a WQL -Filter;
# filter by process name in WQL, by path in PowerShell.
$young = @(Get-CimInstance Win32_Process -Filter "Name='python.exe' OR Name='pythonw.exe'" |
    Where-Object {
        $_.CommandLine -like '*main.py*' -and
        $_.ExecutablePath -like "$root*" -and
        $_.CreationDate -and (((Get-Date) - $_.CreationDate).TotalSeconds -lt 180)
    })
if ($young.Count -gt 0) {
    $age = [int]((Get-Date) - $young[0].CreationDate).TotalSeconds
    Write-Log ("guard: python main.py pid={0} started {1}s ago (<180s), giving it time" -f $young[0].ProcessId, $age)
    exit 0
}

# --- guard: restart lockout (anti-flap), 10 min ---
# Russian locale: timestamps only as ISO-8601 roundtrip + InvariantCulture.
if (Test-Path -LiteralPath $stateFile) {
    try {
        $raw = (Get-Content -LiteralPath $stateFile -TotalCount 1)
        $last = [datetime]::Parse($raw, [Globalization.CultureInfo]::InvariantCulture,
                                  [Globalization.DateTimeStyles]::RoundtripKind)
        $ago = ((Get-Date) - $last).TotalSeconds
        if ($ago -lt 600) {
            Write-Log ("guard: last restart {0}s ago (<600s lockout), skipping" -f [int]$ago)
            exit 0
        }
    } catch {
        Write-Log ("state file unreadable, ignoring: {0}" -f $_.Exception.Message)
    }
}

# --- rotate runtime.out.log before restart (rename only: truncating under a
# live cmd `>>` writer produces a sparse file that never shrinks) ---
$runtimeLog = Join-Path $logDir "runtime.out.log"
if ((Test-Path -LiteralPath $runtimeLog) -and ((Get-Item -LiteralPath $runtimeLog).Length -gt 20MB)) {
    try {
        Remove-Item -LiteralPath "$runtimeLog.1" -Force -ErrorAction SilentlyContinue
        Move-Item -LiteralPath $runtimeLog -Destination "$runtimeLog.1" -Force
        Write-Log "rotated runtime.out.log (>20MB)"
    } catch {
        Write-Log ("runtime.out.log rotation skipped (locked): {0}" -f $_.Exception.Message)
    }
}

# --- restart ---
if ($DryRun) {
    Write-Log "DRYRUN: would invoke restart_app.ps1 -Port $port"
    exit 0
}

Write-Log "all probes failed, invoking restart_app.ps1 -Port $port"
(Get-Date).ToString('o') | Set-Content -LiteralPath $stateFile -Encoding ASCII

$psExe = Join-Path $env:WINDIR "System32\WindowsPowerShell\v1.0\powershell.exe"
$restartScript = Join-Path $root "restart_app.ps1"
& $psExe -NoProfile -ExecutionPolicy Bypass -File $restartScript -Port $port 2>&1 |
    ForEach-Object { Write-Log ("restart_app: {0}" -f $_) }
$code = $LASTEXITCODE
Write-Log "restart_app.ps1 exit code: $code"

if ($code -ne 0) {
    # restart_app gates on "/" for only 40 s; cold start can take longer.
    # Wait up to 120 s more before declaring failure. Never re-invoke restart
    # here - the state-file lockout lets the next 5-min tick retry cleanly.
    $deadline = (Get-Date).AddSeconds(120)
    $ok = $false
    while ((Get-Date) -lt $deadline) {
        Start-Sleep -Seconds 5
        try { $ok = (Invoke-WebRequest -Uri $healthUrl -UseBasicParsing -TimeoutSec 5).StatusCode -eq 200 } catch { $ok = $false }
        if ($ok) { break }
    }
    if ($ok) { Write-Log "post-wait: healthy" }
    else { Write-Log "post-wait: STILL DOWN after extra 120s; next tick retries after lockout" }
}
exit 0
