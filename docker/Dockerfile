# syntax=docker/dockerfile:1

# ── Stage 1: install dependencies ────────────────────────────────────────────
FROM python:3.12-slim AS builder

WORKDIR /build

RUN pip install --no-cache-dir uv

COPY requirements/base.txt .
RUN uv pip install --system --no-cache -r requirements/base.txt

# ── Stage 2: runtime image ────────────────────────────────────────────────────
FROM python:3.12-slim

WORKDIR /app

# Non-root user for security
RUN addgroup --system appgroup && adduser --system --ingroup appgroup appuser

# Copy only the installed packages from the builder stage
COPY --from=builder /usr/local/lib/python3.12/site-packages /usr/local/lib/python3.12/site-packages
COPY --from=builder /usr/local/bin /usr/local/bin

# Copy application source
COPY app/ ./app/

# Create uploads directory and assign ownership before switching user
RUN mkdir -p uploads/screenshots && chown -R appuser:appgroup uploads/

USER appuser

EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1"]
