#!/bin/sh
set -e

# start in backend dir
cd /app/Backend

# start the FastAPI app in background
uvicorn main:app --host 0.0.0.0 --port 8000 &

# wait for /health to become available (timeout ~30s)
count=0
until curl -sSf http://127.0.0.1:8000/health > /dev/null 2>&1 || [ $count -ge 30 ]; do
  echo "Waiting for API to start... ($count)"
  sleep 1
  count=$((count+1))
done

if [ $count -ge 30 ]; then
  echo "API did not start within timeout (30s). Check logs."
  # show some recent logs to help debugging
  ps aux || true
fi

# start the Gradio UI (will call the local API)
python app.py
