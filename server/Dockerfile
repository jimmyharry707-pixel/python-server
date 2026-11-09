# Build the Tabler web UI, then the Python API image.
FROM node:22-bookworm-slim AS web
WORKDIR /web
COPY server/web/package.json server/web/package-lock.json ./
RUN npm ci
COPY server/web/ ./
RUN npm run build

FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

# System deps for psycopg / bcrypt build wheels
RUN apt-get update \
    && apt-get install -y --no-install-recommends build-essential libpq-dev \
    && rm -rf /var/lib/apt/lists/*

COPY server/requirements.txt /app/server/requirements.txt
RUN pip install --no-cache-dir -r /app/server/requirements.txt

COPY server /app/server
# Seed content for the supporter training course (imported once into the DB).
COPY docs/bidder-training /app/server/training_seed
COPY --from=web /web/dist /app/server/web/dist

EXPOSE 8000

# 1 worker is a reasonable default; tune to CPU. TLS is terminated by nginx.
CMD ["uvicorn", "server.main:app", "--host", "0.0.0.0", "--port", "8000"]
