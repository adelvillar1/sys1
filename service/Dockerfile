# sys1 HTTP service image.
#
# Build from the repo root so the wheel build sees `src/`:
#
#   docker build -t sys1-service -f service/Dockerfile .
#
# Run with a bearer token + a mounted log volume so calibration data persists
# across container restarts:
#
#   docker run --rm -p 8400:8400 \
#     -e SYS1_BEARER_TOKEN=$SYS1_BEARER_TOKEN \
#     -e FASTINO_API_KEY=$FASTINO_API_KEY \
#     -e TYPESAFE_API_KEY=$TYPESAFE_API_KEY \
#     -v $PWD/logs:/var/lib/sys1/logs \
#     sys1-service

FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    SYS1_LOG_DIR=/var/lib/sys1/logs

WORKDIR /app

# Install the library from source with the [service] extra (FastAPI + uvicorn).
# Copying just what's needed keeps the build cache warm across service edits.
COPY pyproject.toml README.md CHANGELOG.md LICENSE ./
COPY src/ ./src/
RUN pip install --no-cache-dir '.[service]'

# Then the service app itself (lives outside the wheel).
COPY service/ ./service/

# Run as a non-root user.
RUN useradd --create-home --shell /bin/bash sys1 \
 && mkdir -p /var/lib/sys1/logs \
 && chown -R sys1:sys1 /var/lib/sys1 /app
USER sys1

EXPOSE 8400

# A reasonable production default. Tune with `docker run -e UVICORN_WORKERS=N ...`.
CMD ["sh", "-c", "uvicorn service.main:app --host 0.0.0.0 --port 8400 --workers ${UVICORN_WORKERS:-2}"]
