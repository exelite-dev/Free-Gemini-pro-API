# ─── Stage 1: Builder ─────────────────────────────────────────────────────────
FROM python:3.11-slim AS builder

WORKDIR /build

# Install build dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc g++ libffi-dev libssl-dev curl \
    && rm -rf /var/lib/apt/lists/*

# Install Python dependencies into /install
COPY requirements.txt .
RUN pip install --no-cache-dir --upgrade pip \
    && pip install --no-cache-dir --prefix=/install -r requirements.txt

# ─── Stage 2: Runtime ─────────────────────────────────────────────────────────
FROM python:3.11-slim AS runtime

LABEL maintainer="OmniBridge"
LABEL description="Modular Web-to-API LLM Proxy"
LABEL version="1.0.0"

WORKDIR /app

# Copy installed packages from builder
COPY --from=builder /install /usr/local

# Create non-root user
RUN useradd -m -u 1000 -s /bin/bash omnibridge

# Create data directory with correct permissions
RUN mkdir -p /app/data && chown -R omnibridge:omnibridge /app/data

# Copy application source
COPY --chown=omnibridge:omnibridge . .

# Switch to non-root user
USER omnibridge

# Expose port
EXPOSE 8000

# Health check
HEALTHCHECK --interval=30s --timeout=10s --start-period=15s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/health')" || exit 1

# Run
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1", "--log-level", "info"]
