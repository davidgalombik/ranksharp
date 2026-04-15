# Railway deployment Dockerfile
# Build context is the repo root; backend source lives in ./backend/
FROM mcr.microsoft.com/playwright/python:v1.49.0-jammy

WORKDIR /app

RUN apt-get update && apt-get install -y \
    gcc libpq-dev \
    poppler-utils \
    && rm -rf /var/lib/apt/lists/*

COPY backend/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY backend/ .

ENV PYTHONPATH=/app

CMD ["sh", "-c", "uvicorn api.main:app --host 0.0.0.0 --port ${PORT:-8000}"]
