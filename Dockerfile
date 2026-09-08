FROM python:3.12-slim AS base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends curl libpq-dev gcc \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install -r requirements.txt

COPY app ./app
COPY migrations ./migrations
COPY alembic.ini .
COPY scripts ./scripts

RUN mkdir -p /data/uploads
VOLUME ["/data/uploads"]

# Default target is the API; the worker service in docker-compose
# overrides CMD. Kept as ONE image (not two Dockerfiles) so API and
# worker are guaranteed to run identical code -- they're still deployed
# as separate containers/workloads (assignment requirement), just from a
# shared build artifact.
EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
