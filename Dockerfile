# Multi-stage build: dependencies separate from runtime.
# Default Python 3.12; override with --build-arg PYTHON_VERSION=3.11
ARG PYTHON_VERSION=3.12
FROM python:${PYTHON_VERSION}-slim AS builder

WORKDIR /build
COPY requirements.txt .

# Install dependencies into a target directory for copy to runtime.
RUN pip install --no-cache-dir --target /deps -r requirements.txt

# ---------------------------------------------------------------------------
FROM python:${PYTHON_VERSION}-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

# Non-root user for running the bot.
RUN groupadd --gid 1000 bot && \
    useradd --uid 1000 --gid bot --shell /bin/sh --create-home botuser

WORKDIR /app

# Copy installed packages from builder (exclude tests/dev from requirements).
COPY --from=builder /deps /app/deps
ENV PYTHONPATH=/app/deps

# Application code and config.
COPY app /app/app
COPY config /app/config
COPY bot.py perplexity_client.py /app/

# Optional: requirements for reference (no lock file in repo).
COPY requirements.txt /app/

# Writable dirs used by the bot (paths from config).
RUN mkdir -p /app/data /app/data/uploads /app/data/document_texts /app/data/wizards && \
    chown -R botuser:bot /app

USER botuser

# SIGTERM is handled in app/main.py (reminder scheduler shutdown).
CMD ["python", "bot.py"]
