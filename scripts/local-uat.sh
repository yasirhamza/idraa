#!/usr/bin/env bash
# Spin up a local UAT environment.
#
# Wipes idraa.db, runs migrations from scratch, and starts uvicorn on
# port 8000 with auto-reload. First visit redirects to /setup so you can
# create the admin user fresh each session.
#
# Usage:  ./scripts/local-uat.sh
# Stop:   Ctrl-C
set -euo pipefail

PORT="${PORT:-8000}"
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
DB="$ROOT/idraa.db"

cd "$ROOT"

if lsof -i :"$PORT" -sTCP:LISTEN -t >/dev/null 2>&1; then
  echo "Port $PORT is already in use. Stop the other process or set PORT=<other>." >&2
  exit 1
fi

if [[ -f "$DB" ]]; then
  echo "Wiping $DB"
  rm "$DB"
fi

echo "Running alembic migrations..."
uv run alembic upgrade head >/dev/null

cat <<EOF

────────────────────────────────────────────────────────────
  Local UAT ready
────────────────────────────────────────────────────────────

  Open:  http://localhost:$PORT/

  First visit redirects to /setup. Create your admin user there;
  every subsequent run wipes the DB and starts fresh.

  Ctrl-C to stop.

────────────────────────────────────────────────────────────

EOF

exec uv run uvicorn idraa.app:app --port "$PORT" --reload
