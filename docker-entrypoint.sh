#!/bin/sh
# Idraa container entrypoint.
#
# Runs alembic upgrade head before uvicorn binds, so a fresh deploy
# always lands on the latest schema. `set -e` makes a failed migration
# crash the container — Fly's health probe then fails the deploy and
# automatically rolls back to the previous machine.
set -e

echo "[entrypoint] alembic upgrade head ..."
alembic upgrade head

echo "[entrypoint] starting uvicorn on :8000 ..."
exec uvicorn idraa.app:app --host 0.0.0.0 --port 8000
