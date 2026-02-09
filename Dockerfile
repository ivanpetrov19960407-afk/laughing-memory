# Stage 1: install dependencies
FROM python:3.11-slim AS builder
WORKDIR /build
ENV PYTHONDONTWRITEBYTECODE=1
RUN pip install --no-cache-dir --upgrade pip
COPY requirements.txt .
RUN pip install --no-cache-dir --user -r requirements.txt

# Stage 2: production image
FROM python:3.11-slim AS production
WORKDIR /app
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

# Create non-root user and data dir
RUN groupadd --gid 1000 app && useradd --uid 1000 --gid app --shell /bin/bash --create-home app \
    && mkdir -p /app/data && chown -R app:app /app

# Copy installed packages from builder
COPY --from=builder /root/.local /home/app/.local
ENV PATH=/home/app/.local/bin:$PATH

# Copy application (no secrets; config via ENV at runtime)
COPY --chown=app:app app/ ./app/
COPY --chown=app:app config/ ./config/
COPY --chown=app:app bot.py perplexity_client.py ./
COPY --chown=app:app requirements.txt ./

USER app
# Data paths default to /app/data so they are writable
ENV BOT_DB_PATH=/app/data/bot.db \
    ALLOWLIST_PATH=/app/data/allowlist.json \
    DIALOG_MEMORY_PATH=/app/data/dialog_memory.json \
    WIZARD_STORE_PATH=/app/data/wizards \
    UPLOADS_PATH=/app/data/uploads \
    DOCUMENT_TEXTS_PATH=/app/data/document_texts \
    DOCUMENT_SESSIONS_PATH=/app/data/document_sessions.json \
    CALENDAR_PATH=/app/data/calendar.json \
    ORCHESTRATOR_CONFIG_PATH=/app/config/orchestrator.json

ENTRYPOINT ["python", "-u", "bot.py"]
