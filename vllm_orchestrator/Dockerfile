# vLLM Orchestrator — production image
# Multi-stage build: builder installs deps into venv, final image copies venv + app
#
# Build:   docker build -t vllm-orchestrator:latest .
# Run:     docker run --rm -p 8100:8100 --env-file .env vllm-orchestrator:latest

# ---------- Stage 1: dependencies ----------
FROM python:3.14-slim AS builder

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

# Build tools for any wheels that need compilation
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential curl ca-certificates \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /build

# Copy only dep manifest first (Docker layer cache)
COPY requirements.txt /build/requirements.txt

RUN python -m venv /opt/venv \
    && /opt/venv/bin/pip install --upgrade pip \
    && /opt/venv/bin/pip install -r /build/requirements.txt

# ---------- Stage 2: runtime ----------
FROM python:3.14-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PATH="/opt/venv/bin:$PATH"

# Non-root user for security
RUN groupadd --system app && useradd --system --gid app --uid 1000 --create-home --home-dir /home/app app

# Install runtime tools (curl for healthcheck)
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl tini \
    && rm -rf /var/lib/apt/lists/*

# Copy venv from builder
COPY --from=builder /opt/venv /opt/venv

WORKDIR /app

# Copy application code
COPY --chown=app:app src/ /app/src/
COPY --chown=app:app configs/ /app/configs/
COPY --chown=app:app prompts/ /app/prompts/
COPY --chown=app:app schemas/ /app/schemas/

# Data / logs dir (will be overridden by volume mount)
RUN mkdir -p /data/storage /data/logs /data/training_data /data/adapters /data/benchmark_scores \
    && chown -R app:app /data /app

ENV ORCHESTRATOR_BASE_DIR=/app \
    STORAGE_DIR=/data/storage \
    LOGS_DIR=/data/logs \
    TRAINING_DATA_DIR=/data/training_data \
    ADAPTERS_DIR=/data/adapters \
    BENCHMARK_DIR=/data/benchmark_scores \
    PYTHONPATH=/app \
    UVICORN_WORKERS=4 \
    APP_PORT=8100

USER app

EXPOSE 8100

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD curl -fsS http://localhost:${APP_PORT}/health/ready || exit 1

# tini as PID 1 for proper signal handling (graceful shutdown)
ENTRYPOINT ["/usr/bin/tini", "--"]

# Launch via gunicorn+uvicorn workers (multi-process) or single uvicorn
# For N workers: gunicorn with UvicornWorker
CMD ["sh", "-c", "exec gunicorn src.app.main:app \
    --worker-class uvicorn.workers.UvicornWorker \
    --workers ${UVICORN_WORKERS} \
    --bind 0.0.0.0:${APP_PORT} \
    --timeout 180 \
    --graceful-timeout 60 \
    --access-logfile - \
    --error-logfile -"]
