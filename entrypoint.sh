#!/bin/sh
set -e

# ensure we run from the backend directory
cd /app/Backend || exit 1

echo "=== ENTRYPOINT DEBUG ==="
echo "Working directory: $(pwd)"
echo "Listing /app/Backend:"
ls -al /app/Backend || true
echo "which python: $(which python || true)"
echo "python version: $(python --version 2>&1)"
python -c "import uvicorn; print('uvicorn import ok')" 2>&1 || true

# start the FastAPI app in background
python -m uvicorn main:app --host 0.0.0.0 --port 8000 &
API_PID=$!

echo "Started API with PID $API_PID"

# wait for /health to become available (timeout ~30s)
count=0
until curl -sSf http://127.0.0.1:8000/health > /dev/null 2>&1 || [ $count -ge 30 ]; do
  echo "Waiting for API to start... ($count)"
  sleep 1
  count=$((count+1))
done

if [ $count -ge 30 ]; then
  echo "API did not start within timeout (30s). Check logs."
  ps aux || true
  exit 1
fi

echo "API is healthy, starting Gradio UI."

# exec the Gradio UI (replaces shell)
exec python app.py
