param(
    [int]$Port = 8000,
    [string]$HostName = "127.0.0.1",
    [switch]$SkipCloudflareCheck
)

$ErrorActionPreference = "Stop"
$ProjectDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$EnvPath = Join-Path $ProjectDir ".env"
$VenvDir = Join-Path $ProjectDir ".venv"
$VenvPython = Join-Path $VenvDir "Scripts\python.exe"
$Requirements = Join-Path $ProjectDir "requirements.txt"

function Read-EnvValue {
    param([string]$Name)
    if (-not (Test-Path -LiteralPath $EnvPath)) {
        return ""
    }
    $line = Get-Content -LiteralPath $EnvPath -Encoding UTF8 |
        Where-Object { $_ -match "^\s*$Name\s*=" } |
        Select-Object -Last 1
    if (-not $line) {
        return ""
    }
    return ($line -replace "^\s*$Name\s*=", "").Trim()
}

function Test-WeakToken {
    param([string]$Value)
    if ([string]::IsNullOrWhiteSpace($Value)) {
        return $true
    }
    if ($Value.Length -lt 16) {
        return $true
    }
    return $Value -in @("change-this-owner-token", "change-this-friend-token", "admin", "viewer", "password", "token")
}

function New-ProjectVenv {
    $pyLauncher = Get-Command py -ErrorAction SilentlyContinue
    if ($pyLauncher) {
        & $pyLauncher.Source -3 -m venv $VenvDir
        return
    }

    $python = Get-Command python -ErrorAction SilentlyContinue
    if ($python) {
        & $python.Source -m venv $VenvDir
        return
    }

    throw "Python was not found. Install Python 3.11+ from https://www.python.org/downloads/ and run this script again."
}

function Ensure-AppPython {
    if (-not (Test-Path -LiteralPath $VenvPython)) {
        Write-Host "Creating local .venv ..." -ForegroundColor Cyan
        New-ProjectVenv
    }

    if (-not (Test-Path -LiteralPath $VenvPython)) {
        throw "The virtual environment was created, but .venv\Scripts\python.exe was not found."
    }

    if (-not (Test-Path -LiteralPath $Requirements)) {
        throw "requirements.txt was not found. It must be kept next to share_with_friends.ps1."
    }

    & $VenvPython -c "import fastapi, telethon, aiosqlite, pydantic_settings, httpx, dotenv" *> $null
    if ($LASTEXITCODE -ne 0) {
        Write-Host "Installing Python dependencies from requirements.txt ..." -ForegroundColor Cyan
        $env:PIP_DISABLE_PIP_VERSION_CHECK = "1"
        & $VenvPython -m pip install -r $Requirements
    }

    return $VenvPython
}

Write-Host "Pulse Desk: free friend access" -ForegroundColor Cyan
Write-Host "Project: $ProjectDir"

if (-not (Test-Path -LiteralPath $EnvPath)) {
    Write-Warning ".env was not found. Copy .env.example to .env and fill tokens first."
    exit 1
}

$adminToken = Read-EnvValue -Name "ADMIN_TOKEN"
$viewerToken = Read-EnvValue -Name "VIEWER_TOKEN"
$shareMode = Read-EnvValue -Name "PUBLIC_SHARE_MODE"

if (Test-WeakToken -Value $adminToken) {
    Write-Warning "ADMIN_TOKEN is empty or weak. Set a long private owner token."
}
if (Test-WeakToken -Value $viewerToken) {
    Write-Warning "VIEWER_TOKEN is empty or weak. Set a separate long token for friends."
}
if ($shareMode.ToLowerInvariant() -ne "true") {
    Write-Warning "PUBLIC_SHARE_MODE is not true. Friends can still sign in with VIEWER_TOKEN, but PUBLIC_SHARE_MODE=true is recommended."
}

$cloudflared = Get-Command cloudflared -ErrorAction SilentlyContinue
if (-not $cloudflared) {
    Write-Warning "cloudflared was not found in PATH."
    Write-Host "Install Cloudflare Tunnel CLI, then run this script again:" -ForegroundColor Yellow
    Write-Host "https://developers.cloudflare.com/cloudflare-one/connections/connect-networks/downloads/"
    exit 1
}

$pythonPath = Ensure-AppPython

$localUrl = "http://${HostName}:${Port}"

if (-not $SkipCloudflareCheck) {
    Write-Host "Checking access to api.trycloudflare.com:443 ..." -ForegroundColor Cyan
    $cloudflareReachable = Test-NetConnection -ComputerName "api.trycloudflare.com" -Port 443 -InformationLevel Quiet
    if (-not $cloudflareReachable) {
        Write-Warning "Cannot connect to api.trycloudflare.com:443. Cloudflare Quick Tunnel will not start from this network."
        Write-Host ""
        Write-Host "Try these fixes:" -ForegroundColor Yellow
        Write-Host "1. Turn on a VPN or try another network/mobile hotspot."
        Write-Host "2. Allow cloudflared.exe in Windows Defender Firewall/antivirus."
        Write-Host "3. Check that corporate/provider filtering is not blocking Cloudflare."
        Write-Host "4. Run again with -SkipCloudflareCheck if you think this check is wrong."
        Write-Host ""
        Write-Host "Local app can still be started with: .\.venv\Scripts\python.exe main.py"
        exit 1
    }
}

Write-Host ""
Write-Host "Starting Pulse Desk locally on $localUrl ..." -ForegroundColor Green
$appProcess = Start-Process -FilePath $pythonPath -ArgumentList "main.py" -WorkingDirectory $ProjectDir -PassThru -WindowStyle Hidden
Start-Sleep -Seconds 3

Write-Host "Starting Cloudflare Quick Tunnel. Copy the HTTPS *.trycloudflare.com URL from the output below." -ForegroundColor Green
Write-Host "Share only the URL and VIEWER_TOKEN with friends. Never share ADMIN_TOKEN, .env, .session, .db, or app.log." -ForegroundColor Yellow
Write-Host ""

try {
    & $cloudflared.Source tunnel --url $localUrl
}
finally {
    if ($appProcess -and -not $appProcess.HasExited) {
        Write-Host "Stopping Pulse Desk..." -ForegroundColor Yellow
        Stop-Process -Id $appProcess.Id -Force
    }
}
