#!/usr/bin/env bash
# AgentAssure setup script for Linux / macOS
set -e

echo "=== Setting up AgentAssure ==="

# Check Python version
if ! command -v python3 &> /dev/null; then
    echo "Error: python3 is not installed. Please install Python 3.9 or newer."
    exit 1
fi

PYTHON_VERSION=$(python3 -c 'import sys; print(".".join(map(str, sys.version_info[:2])))')
echo "Detected Python version: $PYTHON_VERSION"

# Create virtual environment if not present
if [ ! -d ".venv" ]; then
    echo "Creating virtual environment in .venv..."
    python3 -m venv .venv
fi

# Activate virtual environment
echo "Activating virtual environment..."
source .venv/bin/activate

# Upgrade pip and install package with dev and ingest dependencies
echo "Installing AgentAssure and dependencies..."
python -m pip install --upgrade pip
pip install -e ".[dev,ingest]"

echo ""
echo "=== Setup complete! ==="
echo "You can now run:"
echo "  source .venv/bin/activate"
echo "  agentassure --help"
echo "  agentassure init"
