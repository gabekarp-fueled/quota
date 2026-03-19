# =============================================================================
# Quota Agent Framework — Multi-stage Dockerfile
# Stage 1: Build React UI
# Stage 2: Install Python dependencies
# Stage 3: Runtime image
# =============================================================================

# ── Stage 1: React build ──────────────────────────────────────────────────────
FROM node:20-alpine AS ui-builder

WORKDIR /app/ui
COPY ui/package*.json ./
RUN npm ci

COPY ui/ ./
RUN npm run build
# Output goes to /app/ui/dist (vite.config.js sets outDir: ../static,
# but we copy from here to keep paths predictable in the runtime stage)


# ── Stage 2: Python dependency install ───────────────────────────────────────
FROM python:3.11-slim AS py-builder

WORKDIR /app
COPY pyproject.toml ./
RUN pip install --upgrade pip && \
    pip install --no-cache-dir -e ".[dev]" --target /app/site-packages || \
    pip install --no-cache-dir \
        fastapi \
        "uvicorn[standard]" \
        anthropic \
        "sqlalchemy[asyncio]" \
        asyncpg \
        "python-jose[cryptography]" \
        "passlib[bcrypt]" \
        httpx \
        python-multipart \
        aiosmtplib \
        email-validator \
        python-dotenv \
        slack-sdk \
        aiofiles \
        pydantic-settings \
        PyJWT


# ── Stage 3: Runtime ─────────────────────────────────────────────────────────
FROM python:3.11-slim AS runtime

# Install system deps for asyncpg
RUN apt-get update && apt-get install -y --no-install-recommends \
    libpq-dev \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Copy Python packages from builder
COPY --from=py-builder /usr/local/lib/python3.11/site-packages /usr/local/lib/python3.11/site-packages
COPY --from=py-builder /usr/local/bin /usr/local/bin

# Copy application source
COPY src/ ./src/
COPY prompts/ ./prompts/

# Copy built React UI into static/ (served by FastAPI StaticFiles at "/")
COPY --from=ui-builder /app/static ./static/

# Create non-root user
RUN useradd -m -u 1001 quota && chown -R quota:quota /app
USER quota

EXPOSE 8000

# Health check
HEALTHCHECK --interval=30s --timeout=10s --start-period=15s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/health')"

CMD ["sh", "-c", "uvicorn src.main:app --host 0.0.0.0 --port ${PORT:-8000} --workers 1"]
