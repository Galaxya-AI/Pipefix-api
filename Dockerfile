FROM python:3.12-slim

# No .pyc files, logs written at once
# PYTHONPATH: app.py and the rq workers import the project modules from src/
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app/src

WORKDIR /app

# Dependencies first, to reuse the Docker cache
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Source code and MCP servers configuration
COPY app.py ./
COPY templates/ templates/
COPY src/ src/
COPY config/ config/

# Unprivileged user, with a writable runs folder
RUN useradd --create-home appuser && mkdir -p /app/runs && chown appuser /app/runs
USER appuser

EXPOSE 8000

# Variables come from the environment (docker run --env-file .env, or docker-compose)
# Default: the REST API on a single gunicorn process, safe without Redis.
# docker-compose.yml sets the production options; the workers override the command.
CMD ["gunicorn", "app:app", "--bind=0.0.0.0:8000", "--threads=4", "--access-logfile=-"]
