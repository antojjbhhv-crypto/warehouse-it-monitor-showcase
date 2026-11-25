FROM python:3.15.1-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

COPY requirements.txt ./requirements.txt
RUN python -m pip install --no-cache-dir -r requirements.txt

COPY app ./app
COPY demo_service ./demo_service
COPY config/services.docker.json ./config/services.docker.json

RUN addgroup --system monitor && adduser --system --ingroup monitor --home /app monitor \
    && mkdir -p /app/data /app/backups \
    && chown -R monitor:monitor /app

USER monitor
EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
