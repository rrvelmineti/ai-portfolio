# syntax=docker/dockerfile:1.7
# Demo app image (ADR 0008). Multi-stage: build the venv and download the models with the
# SAME script Leapcell's build command runs (scripts/build_runtime.sh), then copy only the
# runtime pieces into a clean slim image that runs as a non-root user.
#
#   docker build -t ai-portfolio-demo .
#   docker compose up            # postgres + migrations job + app (see docker-compose.yml)
#
# No secrets are passed at build time. Behind a TLS-intercepting proxy, add
#   --secret id=ca,src=/path/to/ca-bundle.crt
# The bundle is mounted for the build step only and never stored in a layer.

ARG PYTHON_IMAGE=python:3.12-slim-bookworm

FROM ghcr.io/astral-sh/uv:0.13.10 AS uv

FROM ${PYTHON_IMAGE} AS builder
COPY --from=uv /uv /usr/local/bin/uv
WORKDIR /app
COPY pyproject.toml uv.lock ./
COPY core core
COPY scripts scripts
RUN --mount=type=cache,target=/root/.cache/uv \
    --mount=type=secret,id=ca,required=false \
    if [ -f /run/secrets/ca ]; then export SSL_CERT_FILE=/run/secrets/ca; fi; \
    bash scripts/build_runtime.sh

FROM ${PYTHON_IMAGE} AS runtime
RUN useradd --system --uid 10001 --user-group --no-create-home --home-dir /app app
WORKDIR /app
COPY --from=builder --chown=app:app /app/.venv /app/.venv
COPY --from=builder --chown=app:app /app/.models /app/.models
COPY --chown=app:app core/auth/demo_app.py core/auth/demo_app.py
COPY --chown=app:app scripts/start_runtime.sh scripts/migrate_and_bootstrap.py \
     scripts/seed_demo.py scripts/reset_demo.py scripts/
ENV PATH=/app/.venv/bin:$PATH \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    GUARD_MODEL_DIR=/app/.models \
    PORT=8080
USER 10001:10001
EXPOSE 8080
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD ["python", "-c", "import os,urllib.request; urllib.request.urlopen(f\"http://127.0.0.1:{os.environ.get('PORT','8080')}/healthz\", timeout=4)"]
CMD ["bash", "scripts/start_runtime.sh"]
