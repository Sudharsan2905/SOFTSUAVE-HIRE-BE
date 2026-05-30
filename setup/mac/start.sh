#!/usr/bin/env bash
set -e

echo "============================================"
echo " Softsuave Hire BE - Starting (macOS)"
echo "============================================"

cd "$(dirname "$0")/../.."

if [ ! -d ".venv" ]; then
    echo "[ERROR] Virtual environment not found. Run setup.sh first."
    exit 1
fi

source .venv/bin/activate

echo "[INFO] Starting server on http://localhost:8000"
echo "[INFO] Swagger UI: http://localhost:8000/api/docs"
echo "[INFO] Press Ctrl+C to stop"
echo ""

uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
