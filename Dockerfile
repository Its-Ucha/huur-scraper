FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

COPY requirements.txt .
RUN pip install -r requirements.txt

RUN useradd --create-home --uid 1000 app \
    && mkdir -p /app/data \
    && chown app:app /app/data

COPY src ./src

USER app

# Stdout-only logging; the DB lives on the mounted volume.
ENV DATABASE_PATH=/app/data/huur_scraper.db \
    LOG_FILE_PATH= \
    LOG_TO_CONSOLE=true

VOLUME ["/app/data"]

CMD ["python", "-m", "src.bot"]
