#!/bin/sh
# Idraa container entrypoint.
#
# Runs alembic upgrade head before uvicorn binds, so a fresh deploy
# always lands on the latest schema. `set -e` makes a failed migration
# crash the container — the platform's health probe then fails the deploy
# and rolls back to the previous machine.
set -e

echo "[entrypoint] alembic upgrade head ..."
alembic upgrade head

echo "[entrypoint] starting uvicorn on :8000 ..."
# NB: FORWARDED_ALLOW_IPS is honored via uvicorn's env fallback (Config reads
# the bare variable when --forwarded-allow-ips is not passed).
exec uvicorn idraa.app:app --host 0.0.0.0 --port 8000
