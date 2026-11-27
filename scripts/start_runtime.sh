#!/usr/bin/env bash
# Shared start command (ADR 0008), used by the Dockerfile CMD and Leapcell's start command:
#   bash scripts/start_runtime.sh
# The app does NOT run migrations at startup: CI runs them, with the owner role, before
# each deploy. Models load lazily on the first AI call, not here.
set -euo pipefail
cd "$(dirname "$0")/.."

export GUARD_MODEL_DIR="${GUARD_MODEL_DIR:-$PWD/.models}"
VENV="${UV_PROJECT_ENVIRONMENT:-$PWD/.venv}"

exec "$VENV/bin/uvicorn" --factory demo_app:create_app --app-dir core/auth \
    --host 0.0.0.0 --port "${PORT:-8080}" --log-level warning --no-access-log
