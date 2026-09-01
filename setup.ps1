# One-time setup. Run from anywhere — this script locates the project itself:
#   powershell -ExecutionPolicy Bypass -File .\setup.ps1
# or just double-click setup.bat

$ErrorActionPreference = "Stop"
$root = $PSScriptRoot
if (-not $root) { $root = Split-Path -Parent $MyInvocation.MyCommand.Definition }
Set-Location $root

Write-Host ""
Write-Host "=== JobPilot setup ===" -ForegroundColor Cyan
Write-Host "Installing into: $root"
Write-Host ""

# --- find a usable Python ---------------------------------------------------
$launcher = $null
foreach ($candidate in @("py", "python", "python3")) {
    $cmd = Get-Command $candidate -ErrorAction SilentlyContinue
    if ($cmd) {
        $version = & $candidate -c "import sys; print('%d.%d' % sys.version_info[:2])" 2>$null
        if ($LASTEXITCODE -eq 0 -and $version) {
            $parts = $version.Split('.')
            if ([int]$parts[0] -ge 3 -and [int]$parts[1] -ge 10) {
                $launcher = $candidate
                Write-Host "Using Python $version ($($cmd.Source))" -ForegroundColor Green
                break
            }
        }
    }
}
if (-not $launcher) {
    Write-Host "Python 3.10 or newer was not found on PATH." -ForegroundColor Red
    Write-Host "Install it from https://www.python.org/downloads/ and tick" -ForegroundColor Red
    Write-Host "'Add python.exe to PATH' during installation, then re-run this script." -ForegroundColor Red
    if ($Host.Name -eq "ConsoleHost") { Read-Host "Press Enter to close" }
    exit 1
}

$python = Join-Path $root ".venv\Scripts\python.exe"

if (-not (Test-Path $python)) {
    Write-Host "Creating the virtual environment..." -ForegroundColor Yellow
    & $launcher -m venv (Join-Path $root ".venv")
    if ($LASTEXITCODE -ne 0) {
        Write-Host "Could not create the virtual environment." -ForegroundColor Red
        if ($Host.Name -eq "ConsoleHost") { Read-Host "Press Enter to close" }
        exit 1
    }
}

Write-Host "Installing dependencies (this takes a minute)..." -ForegroundColor Yellow
& $python -m pip install --upgrade pip --quiet
& $python -m pip install -r (Join-Path $root "requirements.txt")
if ($LASTEXITCODE -ne 0) {
    Write-Host "Dependency installation failed - see the errors above." -ForegroundColor Red
    if ($Host.Name -eq "ConsoleHost") { Read-Host "Press Enter to close" }
    exit 1
}

Write-Host "Downloading the Chromium build used for form filling..." -ForegroundColor Yellow
& $python -m playwright install chromium
if ($LASTEXITCODE -ne 0) {
    Write-Host "Chromium download failed. Everything except form autofill will still work;" -ForegroundColor Yellow
    Write-Host "re-run '.venv\Scripts\python.exe -m playwright install chromium' later." -ForegroundColor Yellow
}

$envFile = Join-Path $root ".env"
if (-not (Test-Path $envFile)) {
    Copy-Item (Join-Path $root ".env.example") $envFile
    Write-Host "Created .env - add ANTHROPIC_API_KEY there for AI tailoring (optional)." -ForegroundColor Green
}

Write-Host ""
Write-Host "Setup complete. Start JobPilot with:" -ForegroundColor Green
Write-Host "  powershell -ExecutionPolicy Bypass -File `"$root\run.ps1`"" -ForegroundColor Green
Write-Host "or just double-click run.bat" -ForegroundColor Green
Write-Host ""
if ($Host.Name -eq "ConsoleHost") { Read-Host "Press Enter to close" }
