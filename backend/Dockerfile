# Trading Bot Backend - Multi-stage Docker Build
# Supports: API, Worker, Telegram Bot
#
# IMPORTANT: build context must be the PROJECT ROOT (where ./, requirements.txt,
# backend/ and vendor/ live), e.g. `docker build -f backend/Dockerfile .`
# The `backend` Python package is a top-level source package under /app (not an
# installed wheel), so it is imported via PYTHONPATH=/app.

# --- Base Stage ---
FROM python:3.12-slim as base

# Install system dependencies needed to compile numpy/pandas etc.
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    curl \
    git \
    libpq-dev \
    && rm -rf /var/lib/apt/lists/*

# Create app user
RUN useradd -m -u 1000 -s /bin/bash appuser

# Set working directory (source layout: backend/ is importable here)
WORKDIR /app

# Copy dependency manifests first for layer caching.
# requirements.txt is the complete runtime dependency list.
COPY requirements.txt ./

# Install Python runtime dependencies.
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir -r requirements.txt

# openbb builds its static package at import time by writing into its own
# install dir (.build.lock). Bake that build in now (running as root) and
# hand the dir to appuser so the non-root runtime import succeeds.
RUN python -c "from openbb import obb" && \
    chown -R appuser:appuser /usr/local/lib/python3.12/site-packages/openbb

# Copy application code (project root -> /app).
COPY backend/ ./backend/
COPY vendor/ ./vendor/
# Strategy documents for RAG seeding (docs/strategies) ship with the image.
COPY docs/ ./docs/

# Install the vendored TradingAgents package (--no-deps: build-context install
# at the frozen dependency set; the package is pure-Python).
RUN pip install --no-cache-dir --no-deps ./vendor/tradingagents

# Create data directories, writable by the app user.
RUN mkdir -p /app/data /app/logs /app/audit_logs /app/reflections /app/paper_logs && \
    chown -R appuser:appuser /app

# Ensure the `backend` top-level package is importable.
ENV PYTHONPATH=/app

# Switch to app user.
USER appuser

# Expose port.
EXPOSE 8000

# Health check (API stage only makes sense; harmless for others).
HEALTHCHECK --interval=30s --timeout=10s --start-period=40s --retries=3 \
    CMD curl -f "http://localhost:${PORT:-10000}/healthz" || exit 1


# --- API Stage ---
FROM base as api

ENV INSTANCE_ID=api
ENV PYTHONPATH=/app

CMD ["uvicorn", "backend.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "2"]


# --- Worker Stage ---
FROM base as worker

ENV INSTANCE_ID=worker
ENV PYTHONPATH=/app

CMD ["python", "-m", "backend.orchestrator.runner"]


# --- Development Stage ---
FROM base as dev

ENV INSTANCE_ID=dev
ENV PYTHONPATH=/app

# Install dev tools
USER root
RUN pip install --no-cache-dir \
    pytest \
    pytest-asyncio \
    pytest-cov \
    black \
    ruff \
    mypy \
    ipython

USER appuser

CMD ["uvicorn", "backend.main:app", "--host", "0.0.0.0", "--port", "8000", "--reload"]


# --- Telegram Bot Stage ---
# Kept last so a bare `docker build ...` (e.g. Render, which doesn't pass
# --target) yields the telegram bot image. Use --build-target api/worker/dev
# for the other roles.
FROM base as telegram

ENV INSTANCE_ID=telegram
ENV PYTHONPATH=/app

CMD ["python", "-m", "backend.telegram.bot"]


# --- Cloud Combined Stage (DEFAULT build target) ---
# Runs FastAPI (port from $PORT, default 10000 for Render) + the Telegram
# poller in one process so a single free Render web service is healthy (via
# /healthz), kept awake by a heartbeat, and answers Telegram — no worker pserv.
FROM base as cloud

ENV INSTANCE_ID=cloud
ENV PYTHONPATH=/app

EXPOSE 8000

CMD ["python", "-m", "backend.run_cloud"]