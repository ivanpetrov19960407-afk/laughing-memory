# Secretary bot: Python slim + tesseract for OCR
FROM python:3.11-slim

# Tesseract for OCR (images → text)
RUN apt-get update && apt-get install -y --no-install-recommends \
    tesseract-ocr \
    tesseract-ocr-rus \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Non-root user
RUN groupadd --gid 1000 app && useradd --uid 1000 --gid 1000 --shell /bin/false app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app
COPY config ./config
COPY bot.py .

# Data dir writable by app user
RUN mkdir -p /app/data && chown -R app:app /app

USER app

ENV PYTHONUNBUFFERED=1
# Default paths inside container
ENV BOT_DB_PATH=/app/data/bot.db
ENV ALLOWLIST_PATH=/app/data/allowlist.json
ENV DIALOG_MEMORY_PATH=/app/data/dialog_memory.json
ENV WIZARD_STORE_PATH=/app/data/wizards
ENV ORCHESTRATOR_CONFIG_PATH=/app/config/orchestrator.json

# .env is not copied; pass BOT_TOKEN etc. via env or compose env_file
CMD ["python", "bot.py"]
