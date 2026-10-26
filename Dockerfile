FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app/src

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app.py ./
COPY templates/ templates/
COPY src/ src/
COPY config/ config/

RUN useradd --create-home appuser && mkdir -p /app/runs && chown appuser /app/runs
USER appuser

EXPOSE 8000

CMD ["gunicorn", "app:app", "--bind=0.0.0.0:8000", "--threads=4", "--access-logfile=-"]
