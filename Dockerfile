FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    TZ=Asia/Tashkent \
    DB_PATH=/app/data/dogovor.db

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app
COPY scripts ./scripts

RUN useradd --system --uid 10001 bot \
    && mkdir -p /app/data /app/backups \
    && chown -R bot /app/data /app/backups
USER bot

VOLUME ["/app/data"]
CMD ["python", "-m", "app"]
