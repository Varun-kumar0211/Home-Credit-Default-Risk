#!/bin/sh
set -e

# Run from the project root so serialized Backend.* model classes can be imported.
cd /app || exit 1

API_PORT="${API_PORT:-8000}"

echo "=== ENTRYPOINT DEBUG ==="
echo "Working directory: $(pwd)"
echo "Listing /app/Backend:"
ls -al /app/Backend || true
echo "which python: $(which python || true)"
echo "python version: $(python --version 2>&1)"
python -c "import uvicorn; print('uvicorn import ok')" 2>&1 || true

echo "Starting API on port $API_PORT..."

# Start the FastAPI app natively (render free tier compatible)
exec python -m uvicorn Backend.main:app --host 0.0.0.0 --port "$API_PORT"
