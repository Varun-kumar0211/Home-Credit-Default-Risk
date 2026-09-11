#!/bin/sh
set -e

# Run from the project root so serialized Backend.* model classes can be imported.
cd /app || exit 1

API_PORT="${API_PORT:-8000}"
STARTUP_TIMEOUT="${API_STARTUP_TIMEOUT:-180}"
API_LOG="/tmp/credit-api.log"

echo "=== ENTRYPOINT DEBUG ==="
echo "Working directory: $(pwd)"
echo "Listing /app/Backend:"
ls -al /app/Backend || true
echo "which python: $(which python || true)"
echo "python version: $(python --version 2>&1)"
python -c "import uvicorn; print('uvicorn import ok')" 2>&1 || true

# Start the FastAPI app in background. Keep its log available if startup fails.
python -m uvicorn Backend.main:app --host 0.0.0.0 --port "$API_PORT" >"$API_LOG" 2>&1 &
API_PID=$!

echo "Started API with PID $API_PID"

# Wait for model imports and API startup. Render can take longer on a cold start.
count=0
until curl -sSf "http://127.0.0.1:${API_PORT}/health" > /dev/null 2>&1; do
  if ! kill -0 "$API_PID" 2>/dev/null; then
    echo "API process exited before becoming healthy. API log:"
    cat "$API_LOG" || true
    exit 1
  fi
  if [ "$count" -ge "$STARTUP_TIMEOUT" ]; then
    echo "API did not start within ${STARTUP_TIMEOUT}s. API log:"
    cat "$API_LOG" || true
    ps aux || true
    exit 1
  fi
  echo "Waiting for API to start... (${count}/${STARTUP_TIMEOUT})"
  sleep 1
  count=$((count+1))
done

echo "API is healthy, starting Gradio UI."

# exec the Gradio UI (replaces shell)
exec python Backend/app.py
