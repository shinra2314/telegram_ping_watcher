# Builds a clean zip of the project for handing off to a friend:
# copies the CURRENT working tree (including uncommitted changes) while
# excluding every confidential file, verifies nothing secret leaked into
# the copy, then archives it to dist\pulse_desk_clean_<timestamp>.zip.
#
# Usage (from anywhere):
#   .\handoff\pack_for_friend.ps1
#   .\handoff\pack_for_friend.ps1 -OutDir "D:\somewhere"

param(
    [string]$OutDir = ""
)

$ErrorActionPreference = "Stop"

$HandoffDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$ProjectDir = Split-Path -Parent $HandoffDir
if (-not $OutDir) { $OutDir = Join-Path $ProjectDir "dist" }

$stamp   = Get-Date -Format "yyyy-MM-dd_HH-mm"
$name    = "pulse_desk_clean_$stamp"
$staging = Join-Path $env:TEMP $name
$zipPath = Join-Path $OutDir "$name.zip"

# Directories excluded by name at any depth.
$excludeDirs = @(
    ".git", ".venv", "__pycache__", ".pytest_cache", ".ruff_cache",
    ".mypy_cache", ".claude", ".codex-empty-sessions", "sessions",
    "logs", "backups", "data", "dist", "htmlcov"
)

# Files excluded by name/pattern at any depth.
$excludeFiles = @(
    ".env", "*.session", "*.session-journal", "*.db", "*.db-*",
    "*.sqlite", "*.sqlite3", "*.log", "mentions*.jsonl",
    ".coverage", "coverage.xml", "services.json"
)

# Patterns that must never appear in the final copy (verified after copying;
# .env.example and services.example.json do NOT match these).
$leakPatterns = @(
    ".env", "*.session", "*.session-journal", "*.db", "*.sqlite",
    "*.sqlite3", "services.json"
)

if (Test-Path $staging) { Remove-Item $staging -Recurse -Force }
New-Item -ItemType Directory -Force -Path $OutDir | Out-Null

Write-Host "Copying clean project tree -> $staging" -ForegroundColor Cyan
robocopy $ProjectDir $staging /E /XD @excludeDirs /XF @excludeFiles /NFL /NDL /NJH /NJS /NP | Out-Null
if ($LASTEXITCODE -ge 8) {
    throw "robocopy failed with exit code $LASTEXITCODE"
}

Write-Host "Verifying no confidential files leaked into the copy..." -ForegroundColor Cyan
$leaks = Get-ChildItem -Path $staging -Recurse -Force -File | Where-Object {
    $file = $_
    ($leakPatterns | Where-Object { $file.Name -like $_ }).Count -gt 0
}
if ($leaks) {
    $leaks | ForEach-Object { Write-Warning "LEAK: $($_.FullName)" }
    Remove-Item $staging -Recurse -Force
    throw "Confidential files found in the staging copy. Zip NOT created."
}

# Empty runtime dirs so the friend's first run has the expected layout.
foreach ($dir in @("sessions", "data", "logs")) {
    New-Item -ItemType Directory -Force -Path (Join-Path $staging $dir) | Out-Null
}

Write-Host "Creating archive..." -ForegroundColor Cyan
if (Test-Path $zipPath) { Remove-Item $zipPath -Force }
Compress-Archive -Path $staging -DestinationPath $zipPath
Remove-Item $staging -Recurse -Force

$sizeMb = [math]::Round((Get-Item $zipPath).Length / 1MB, 1)
Write-Host ""
Write-Host "Done: $zipPath ($sizeMb MB)" -ForegroundColor Green
Write-Host "Send this zip to your friend. Setup guide is inside:" -ForegroundColor Green
Write-Host "  $name\handoff\ИНСТРУКЦИЯ_ДЛЯ_ДРУГА.md"
Write-Host ""
Write-Host "Never send separately: .env, *.session, *.db, logs, backups, ADMIN_TOKEN." -ForegroundColor Yellow

# robocopy leaves 1-7 in $LASTEXITCODE on success; report success explicitly.
exit 0
