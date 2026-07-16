#!/bin/sh
set -e

# ensure we run from the backend directory
cd /app/Backend || exit 1

# start the FastAPI app in background
uvicorn main:app --host 0.0.0.0 --port 8000 &

# wait for /health to become available (timeout ~30s)
count=0
until curl -sSf http://127.0.0.1:8000/health > /dev/null 2>&1 || [ $count -ge 30 ]; do
  echo "Waiting for API to start... ($count)"
  sleep 1
  count=$((count+1))


if [ $count -ge 30 ]; then
  echo "API did not start within timeout (30s). Check logs."
  ps aux || true
fi

# exec the Gradio UI (replaces shell)
exec python app.py
python app.py
