#!/usr/bin/env bash
set -e

echo "============================================"
echo " Softsuave Hire BE - Linux Setup"
echo "============================================"

# Move to project root (two levels up from setup/linux/)
cd "$(dirname "$0")/../.."

# Install uv if not present
if ! command -v uv &>/dev/null; then
    echo "[INFO] uv not found. Installing uv..."
    curl -LsSf https://astral.sh/uv/install.sh | sh
    export PATH="$HOME/.local/bin:$PATH"
else
    echo "[OK] uv found — $(uv --version)"
fi

# Create virtual environment pinned to Python 3.12
if [ ! -d ".venv" ]; then
    echo "[INFO] Creating virtual environment with Python 3.12..."
    uv venv --python 3.12
else
    echo "[OK] Virtual environment already exists"
fi

# Activate virtual environment
source .venv/bin/activate

# Install all dependencies (base + dev)
echo "[INFO] Installing dependencies..."
uv pip install -r requirements/dev.txt
echo "[OK] Dependencies installed"

# Copy .env if not present
if [ ! -f ".env" ]; then
    cp .env.example .env
    echo "[OK] .env created from .env.example"
    echo "[ACTION] Open .env and fill in your secrets before starting"
else
    echo "[OK] .env already exists"
fi

# Install pre-commit hooks
echo "[INFO] Installing pre-commit hooks..."
pre-commit install
echo "[OK] Pre-commit hooks installed"

echo ""
echo "============================================"
echo " Setup complete!"
echo " Next: bash setup/linux/start.sh"
echo "============================================"
