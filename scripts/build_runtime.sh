#!/usr/bin/env bash
# Shared runtime build steps (ADR 0008). Used by BOTH the Dockerfile and Leapcell's build
# command, so the two recipes cannot drift:
#   Dockerfile:  RUN scripts/build_runtime.sh
#   Leapcell:    build command  "bash scripts/build_runtime.sh"
#
# What it does, from the repository root:
#   1. installs uv (pinned) if it is not already on PATH;
#   2. installs the locked runtime dependencies into .venv (no dev tools; spaCy
#      en_core_web_lg comes pinned by URL from uv.lock; uvicorn from the "serve" group);
#   3. downloads the guard-small injection classifier into .models and verifies every
#      file against its pinned SHA-256 (scripts/download_guard_model.py).
# Both models end up inside the image, so nothing is downloaded at startup.
set -euo pipefail

UV_VERSION="0.13.10"
cd "$(dirname "$0")/.."

export UV_PROJECT_ENVIRONMENT="${UV_PROJECT_ENVIRONMENT:-$PWD/.venv}"
export GUARD_MODEL_DIR="${GUARD_MODEL_DIR:-$PWD/.models}"
# If the platform has no Python 3.12, uv installs one inside the project directory so it
# ships with the build instead of living in a home directory outside the image.
export UV_PYTHON_INSTALL_DIR="${UV_PYTHON_INSTALL_DIR:-$PWD/.python}"
export UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy

if ! command -v uv >/dev/null 2>&1 || [ "$(uv --version | cut -d' ' -f2)" != "$UV_VERSION" ]; then
    python3 -m pip install --no-cache-dir --quiet "uv==${UV_VERSION}"
fi

uv sync --frozen --no-dev --group nlp-models --group serve --no-editable
"$UV_PROJECT_ENVIRONMENT/bin/python" scripts/download_guard_model.py
echo "build_runtime: venv at $UV_PROJECT_ENVIRONMENT, guard model at $GUARD_MODEL_DIR"
