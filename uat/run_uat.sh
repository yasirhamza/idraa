#!/usr/bin/env bash
# uat/run_uat.sh — bootstrap a fresh UAT environment and run the smoke pass.
#
# Creates an ephemeral SQLite DB, runs migrations, starts uvicorn,
# runs the Playwright script, captures findings + screenshots, tears
# down the server.
#
# Usage:  ./uat/run_uat.sh
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

UAT_DB="/tmp/idraa-uat-$(date +%s).db"
UAT_SECRET="$(.venv/bin/python -c 'import secrets; print(secrets.token_urlsafe(48))')"

export DATABASE_URL="sqlite+aiosqlite:///$UAT_DB"
export SESSION_SECRET="$UAT_SECRET"
export ENVIRONMENT=dev

echo "UAT DB:     $UAT_DB"
echo "Repo root:  $REPO_ROOT"
echo ""

echo "[1/4] Running alembic upgrade head against fresh DB..."
.venv/bin/alembic upgrade head 2>&1 | tail -2

echo ""
echo "[2/4] Starting uvicorn on port 8000..."
.venv/bin/uvicorn idraa.app:app --host 127.0.0.1 --port 8000 --log-level info \
  > /tmp/uat-uvicorn.log 2>&1 &
UVICORN_PID=$!
trap "echo ''; echo '[cleanup] killing uvicorn PID $UVICORN_PID'; kill $UVICORN_PID 2>/dev/null; wait $UVICORN_PID 2>/dev/null; rm -f $UAT_DB" EXIT

for i in {1..20}; do
  if curl -sf http://localhost:8000/healthz > /dev/null; then
    echo "      server ready"
    break
  fi
  sleep 0.5
done

echo ""
echo "[3/4] Running Playwright smoke pass..."
.venv/bin/python uat/test_mvp_smoke.py
EXIT_CODE=$?

echo ""
echo "[4/4] Last 30 lines of uvicorn log:"
tail -30 /tmp/uat-uvicorn.log

exit $EXIT_CODE
