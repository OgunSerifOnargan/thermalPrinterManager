# Aco Recycling Thermal Printer Service — service container.
#
# Intended to run alongside the mock-device container via docker-compose
# (TRANSPORT_BACKEND=real, LAN_HOST=mock-device:9100). For a real Cashino
# over LAN, set LAN_HOST to the printer's IP. USB transport is NOT
# supported in Docker on macOS/Windows — use --device on Linux if you
# need USB inside the container.

FROM python:3.13-slim AS base

# Non-root user
RUN groupadd -r aco && useradd -r -g aco -d /app -s /sbin/nologin aco

WORKDIR /app

# Install only the runtime deps first (better layer caching)
COPY requirements.txt ./
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir -r requirements.txt

# Copy app source
COPY app/        app/
COPY scripts/    scripts/
COPY .env.example ./

# Prepare runtime dirs and ownership
RUN mkdir -p /app/logs /app/data && \
    chown -R aco:aco /app

USER aco

# Sensible defaults for a containerized run. docker-compose can override
# LAN_HOST=mock-device etc.; bare `docker run` users must supply -e or
# mount a .env at /app/.env.
ENV HOST=0.0.0.0 \
    PORT=8000 \
    LOG_DIR=/app/logs \
    JOBS_DB_PATH=/app/data/jobs.db \
    TRANSPORT_BACKEND=real \
    DEFAULT_MODE= \
    LAN_PORT=9100

EXPOSE 8000

# /healthz is the deep liveness probe — it touches the DB, verifies the
# reconcile loop is alive, and (when connected) checks link freshness.
# Returns 503 if any sub-system is degraded so orchestrators see real
# health, not just "process is running". /health remains a shallow ping.
HEALTHCHECK --interval=30s --timeout=3s --start-period=10s --retries=3 \
  CMD python -c "import urllib.request,sys; urllib.request.urlopen('http://127.0.0.1:8000/healthz').status==200 or sys.exit(1)" \
  || exit 1

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
