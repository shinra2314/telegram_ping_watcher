param(
    [switch]$SkipInstall
)

$ErrorActionPreference = "Stop"
$ProjectDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$VenvDir = Join-Path $ProjectDir ".venv"
$VenvPython = Join-Path $VenvDir "Scripts\python.exe"
$Requirements = Join-Path $ProjectDir "requirements.txt"
$EnvExample = Join-Path $ProjectDir ".env.example"
$EnvFile = Join-Path $ProjectDir ".env"

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

Set-Location -LiteralPath $ProjectDir

if (-not (Test-Path -LiteralPath $Requirements)) {
    throw "requirements.txt was not found next to this script."
}

if (-not (Test-Path -LiteralPath $VenvPython)) {
    Write-Host "Creating local .venv ..." -ForegroundColor Cyan
    New-ProjectVenv
}

if (-not (Test-Path -LiteralPath $VenvPython)) {
    throw "The virtual environment was created, but .venv\Scripts\python.exe was not found."
}

if (-not $SkipInstall) {
    Write-Host "Installing Python dependencies from requirements.txt ..." -ForegroundColor Cyan
    $env:PIP_DISABLE_PIP_VERSION_CHECK = "1"
    & $VenvPython -m pip install -r $Requirements
}

if (-not (Test-Path -LiteralPath $EnvFile) -and (Test-Path -LiteralPath $EnvExample)) {
    Copy-Item -LiteralPath $EnvExample -Destination $EnvFile
    Write-Host "Created .env from .env.example. Fill TELEGRAM_API_ID, TELEGRAM_API_HASH and tokens before real monitoring." -ForegroundColor Yellow
}

Write-Host ""
Write-Host "Starting Pulse Desk with the project interpreter:" -ForegroundColor Green
Write-Host $VenvPython
Write-Host "Open http://127.0.0.1:8000 after the server starts."
Write-Host ""

& $VenvPython main.py
