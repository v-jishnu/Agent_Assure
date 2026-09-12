# AgentAssure setup script for Windows PowerShell
$ErrorActionPreference = "Stop"

Write-Host "=== Setting up AgentAssure ===" -ForegroundColor Cyan

# Check Python installation
$pythonCmd = Get-Command python -ErrorAction SilentlyContinue
if (-not $pythonCmd) {
    Write-Error "Python is not installed or not found in PATH. Please install Python 3.9 or newer."
    exit 1
}

$pyVersion = python -c "import sys; print('.'.join(map(str, sys.version_info[:2])))"
Write-Host "Detected Python version: $pyVersion" -ForegroundColor Green

# Create virtual environment if missing
if (-not (Test-Path ".venv")) {
    Write-Host "Creating virtual environment in .venv..." -ForegroundColor Yellow
    python -m venv .venv
}

# Activate virtual environment
Write-Host "Activating virtual environment..." -ForegroundColor Yellow
& .\.venv\Scripts\Activate.ps1

# Upgrade pip and install package
Write-Host "Installing AgentAssure and dependencies..." -ForegroundColor Yellow
python -m pip install --upgrade pip
pip install -e ".[dev,ingest]"

Write-Host "`n=== Setup complete! ===" -ForegroundColor Cyan
Write-Host "You can now run:"
Write-Host "  .\.venv\Scripts\Activate.ps1"
Write-Host "  agentassure --help"
Write-Host "  agentassure init"
