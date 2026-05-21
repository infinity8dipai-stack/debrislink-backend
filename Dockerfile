# syntax=docker/dockerfile:1.6
# =============================================================================
# DebrisLink — Production Container
# =============================================================================
# Base:     python:3.10-slim         (small, well-maintained, glibc-based)
# Driver:   psycopg2-binary          (ships its own libpq — no apt deps)
# Server:   uvicorn --workers $WEB_CONCURRENCY
# User:     non-root (uid 10001)
# Port:     8000  (respects $PORT if the platform injects one)
# =============================================================================

FROM python:3.10-slim

# --- Python runtime hygiene ---
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

# --- Non-root user for security compliance ---
ARG APP_UID=10001
RUN groupadd --gid ${APP_UID} app \
 && useradd  --uid ${APP_UID} --gid app --shell /bin/bash --create-home app

WORKDIR /app

# --- Install Python deps in a dedicated layer so it caches across code edits ---
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

# --- Copy the application source, owned by the runtime user ---
COPY --chown=app:app . .

# --- Ensure runtime-writable directories exist and are owned by the app user ---
RUN mkdir -p /app/storage/certificates \
 && chown -R app:app /app/storage

# Drop privileges before runtime.
USER app

EXPOSE 8000

# Default to 2 workers; cloud platforms typically override via env var.
ENV WEB_CONCURRENCY=2

# Shell form so $PORT (Render/Railway inject this) and $WEB_CONCURRENCY
# expand at container start. `exec` makes uvicorn PID 1 so SIGTERM forwards
# cleanly during platform-level graceful shutdowns.
CMD ["sh", "-c", "exec uvicorn main:app --host 0.0.0.0 --port ${PORT:-8000} --workers ${WEB_CONCURRENCY:-2}"]
