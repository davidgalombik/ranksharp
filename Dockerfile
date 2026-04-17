# Railway deployment Dockerfile
# Build context is the repo root; backend source lives in ./backend/
FROM mcr.microsoft.com/playwright/python:v1.49.0-jammy

WORKDIR /app

# Install poppler-utils (for pdf2image). Retry up to 3 times with a
# per-attempt timeout because Railway's build network sometimes routes
# to extremely slow or unreachable Ubuntu mirrors and apt-get can hang
# for ages instead of failing fast. psycopg2-binary is used instead of
# psycopg2, so we don't need gcc / libpq-dev.
#
# Uses `set +e` so that a failed attempt doesn't abort the loop, and
# `timeout 120` so each attempt gives up after 2 minutes.
RUN set +e; \
    for i in 1 2 3; do \
      timeout 120 apt-get update && \
      timeout 120 apt-get install -y --no-install-recommends poppler-utils && \
      rm -rf /var/lib/apt/lists/* && \
      exit 0; \
      echo ">>> apt-get attempt $i failed/timed out, retrying in 10s..."; \
      sleep 10; \
    done; \
    echo ">>> apt-get failed after 3 attempts" >&2; \
    exit 1

COPY backend/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY backend/ .

ENV PYTHONPATH=/app

CMD ["sh", "-c", "uvicorn api.main:app --host 0.0.0.0 --port ${PORT:-8000}"]
