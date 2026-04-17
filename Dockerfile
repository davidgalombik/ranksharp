# Railway deployment Dockerfile
# Build context is the repo root; backend source lives in ./backend/
FROM mcr.microsoft.com/playwright/python:v1.49.0-jammy

WORKDIR /app

# Install poppler-utils (for pdf2image). Retry a few times because
# Railway's build network occasionally can't reach the Ubuntu mirrors.
# psycopg2-binary is used instead of psycopg2, so we don't need gcc/libpq-dev.
RUN for i in 1 2 3 4 5; do \
      apt-get update && \
      apt-get install -y --no-install-recommends poppler-utils && \
      rm -rf /var/lib/apt/lists/* && \
      break; \
      echo "apt-get attempt $i failed, retrying in 15s..." && sleep 15; \
    done

COPY backend/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY backend/ .

ENV PYTHONPATH=/app

CMD ["sh", "-c", "uvicorn api.main:app --host 0.0.0.0 --port ${PORT:-8000}"]
