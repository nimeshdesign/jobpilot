# Start JobPilot. It opens http://127.0.0.1:8765 in your browser.
# Works no matter which directory you run it from.

$ErrorActionPreference = "Stop"
$root = $PSScriptRoot
if (-not $root) { $root = Split-Path -Parent $MyInvocation.MyCommand.Definition }
Set-Location $root

$python = Join-Path $root ".venv\Scripts\python.exe"

if (-not (Test-Path $python)) {
    Write-Host ""
    Write-Host "JobPilot is not set up yet in:" -ForegroundColor Yellow
    Write-Host "  $root"
    Write-Host ""
    Write-Host "Run this first:" -ForegroundColor Yellow
    Write-Host "  powershell -ExecutionPolicy Bypass -File `"$root\setup.ps1`""
    Write-Host ""
    if ($Host.Name -eq "ConsoleHost") { Read-Host "Press Enter to close" }
    exit 1
}

Write-Host ""
Write-Host "Starting JobPilot from $root" -ForegroundColor Cyan
Write-Host "Your browser will open at http://127.0.0.1:8765" -ForegroundColor Cyan
Write-Host "Keep THIS WINDOW OPEN while you use it. Press Ctrl+C to stop." -ForegroundColor Cyan
Write-Host ""

& $python -m app.main
$code = $LASTEXITCODE

# Always report, whatever happened. A window that vanishes the moment the
# server exits is indistinguishable from one that never started.
Write-Host ""
Write-Host "---------------------------------------------------------------"
if ($code -ne 0) {
    Write-Host "JobPilot exited with code $code - see the message above." -ForegroundColor Red
    Write-Host "Missing package? Re-run setup.ps1." -ForegroundColor Red
} else {
    Write-Host "JobPilot has stopped. The reason is shown above." -ForegroundColor Yellow
}
Write-Host "---------------------------------------------------------------"
Write-Host ""
if ($Host.Name -eq "ConsoleHost") { Read-Host "Press Enter to close" }
exit $code
