FROM python:3.12-slim AS builder

WORKDIR /build
COPY requirements.txt .
RUN pip install --upgrade pip && \
    pip install --no-cache-dir -r requirements.txt

# ---- Runtime image ----
FROM python:3.12-slim AS runtime

RUN useradd --create-home --shell /bin/bash appuser

WORKDIR /app

# Copy installed packages from builder stage
COPY --from=builder /usr/local/lib/python3.12/site-packages /usr/local/lib/python3.12/site-packages
COPY --from=builder /usr/local/bin /usr/local/bin

# Copy application source
COPY --chown=appuser:appuser . .

USER appuser

ENV PORT=8080
EXPOSE 8080

# --workers 1  : one worker so APScheduler fires exactly once
# --threads 4  : concurrent Slack event handling within the single worker
# --timeout 120: Claude API calls can take up to ~60s; Gunicorn default (30s) is too short
# --preload    : load app before forking so scheduler starts once in master
CMD gunicorn \
    --workers 1 \
    --threads 4 \
    --timeout 120 \
    --preload \
    --bind "0.0.0.0:${PORT}" \
    --access-logfile - \
    --error-logfile - \
    main:flask_app
