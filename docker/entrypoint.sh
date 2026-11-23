#!/bin/sh
# Starts uvicorn. More than one worker (or instance) is only valid with Redis, because
# WebSocket sessions/pushes are coordinated through it — refuse to boot half-broken.
set -eu

WORKERS="${UVICORN_WORKERS:-1}"

if [ "$WORKERS" -gt 1 ] && [ -z "${REDIS_URL:-}" ]; then
  echo "FATAL: UVICORN_WORKERS=$WORKERS requires REDIS_URL (WebSocket state is shared via Redis)." >&2
  echo "       Set REDIS_URL, or run with UVICORN_WORKERS=1." >&2
  exit 1
fi

# --no-access-log + the token redaction filter keep WebSocket JWTs out of the logs.
exec uvicorn app.main:app --host 0.0.0.0 --port 8000 --workers "$WORKERS" \
  --proxy-headers --forwarded-allow-ips "*" --no-access-log \
  --timeout-graceful-shutdown 20 --limit-concurrency 5000
