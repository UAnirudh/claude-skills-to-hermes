# Skill Bridge — one-command setup (Windows PowerShell)
$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $Root

Write-Host "==> Skill Bridge setup" -ForegroundColor Cyan
Write-Host ""

if (-not (Get-Command python -ErrorAction SilentlyContinue)) {
    Write-Host "ERROR: Python is required. Install Python 3.10+ and retry." -ForegroundColor Red
    exit 1
}

$pyver = python -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')"
Write-Host "Python: $pyver"

if (-not (Test-Path ".venv")) {
    Write-Host "==> Creating virtual environment (.venv)" -ForegroundColor Cyan
    python -m venv .venv
}

& .\.venv\Scripts\Activate.ps1

Write-Host "==> Installing skill-bridge with watch + dev extras" -ForegroundColor Cyan
python -m pip install --upgrade pip
python -m pip install -e ".[all]"

Write-Host "==> Running tests" -ForegroundColor Cyan
pytest -q

if (-not (Test-Path "skill-bridge.yaml")) {
    Write-Host "==> Creating skill-bridge.yaml from example" -ForegroundColor Cyan
    Copy-Item skill-bridge.yaml.example skill-bridge.yaml
    Write-Host "    Edit skill-bridge.yaml to match your agent paths, then run:"
    Write-Host "    skill-bridge sync"
} else {
    Write-Host "==> skill-bridge.yaml already exists (skipped)" -ForegroundColor Yellow
}

Write-Host ""
Write-Host "Done! Activate the venv and run:" -ForegroundColor Green
Write-Host "  .\.venv\Scripts\Activate.ps1"
Write-Host "  skill-bridge --help"
Write-Host "  skill-bridge detect"
Write-Host "  skill-bridge init          # regenerate config"
Write-Host "  skill-bridge sync          # one-shot sync"
Write-Host "  skill-bridge watch         # live sync"
