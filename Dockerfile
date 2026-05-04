# syntax=docker/dockerfile:1.7-labs
# Multi-stage build for the bot. SPEC §7.9.6.

FROM python:3.14-slim AS builder

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_LINK_MODE=copy \
    UV_COMPILE_BYTECODE=0

RUN apt-get update && apt-get install -y --no-install-recommends \
        build-essential \
        ca-certificates \
        curl \
    && rm -rf /var/lib/apt/lists/*

# Install uv
COPY --from=ghcr.io/astral-sh/uv:0.5.4 /uv /usr/local/bin/uv

WORKDIR /app

# Step 1: install dependencies only (no project) to maximize layer caching.
# `--no-install-project` skips building our own package, so we don't need src/ or README yet.
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-cache --no-dev --no-install-project

# Step 2: copy the project files needed to build music-bot itself, then sync.
COPY README.md ./
COPY src ./src
RUN uv sync --frozen --no-cache --no-dev


# ---------- Runtime ----------
FROM python:3.14-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONMALLOC=malloc \
    MALLOC_TRIM_THRESHOLD_=131072 \
    PATH="/app/.venv/bin:${PATH}"

RUN apt-get update && apt-get install -y --no-install-recommends \
        ca-certificates \
        libstdc++6 \
    && rm -rf /var/lib/apt/lists/* \
    && find /usr/local/lib/python3.12 -type d -name "__pycache__" -exec rm -rf {} + 2>/dev/null || true \
    && rm -rf /usr/share/doc /usr/share/man /usr/share/locale /usr/share/info

WORKDIR /app

COPY --from=builder /app/.venv /app/.venv
COPY --from=builder /app/src /app/src

# Non-root user.
RUN useradd --create-home --shell /usr/sbin/nologin app && chown -R app:app /app
USER app

CMD ["python", "-m", "music_bot"]
