# =============================================================================
# Stage 1: Builder — install dependencies
# =============================================================================
FROM python:3.11-slim AS builder

WORKDIR /build

# Install build tooling
RUN pip install --no-cache-dir hatchling==1.27.0

# Copy dependency manifest first for layer caching
COPY pyproject.toml ./
# Create a minimal src stub so hatchling can resolve the package
RUN mkdir -p src/cachegate && touch src/cachegate/__init__.py

# Install runtime deps into a prefix so we can copy them to the final image
RUN pip install --no-cache-dir --prefix=/install \
    fastapi==0.143.0 \
    uvicorn[standard]==0.54.0 \
    pydantic==2.14.1 \
    pydantic-settings==2.15.0 \
    httpx==0.28.1 \
    redis==8.1.0 \
    structlog==26.1.0 \
    prometheus-client==0.26.0 \
    tiktoken==0.14.0 \
    PyYAML==6.0.3

# =============================================================================
# Stage 2: Final — slim runtime image
# =============================================================================
FROM python:3.11-slim AS final

# Security: non-root user
RUN groupadd --system cachegate && useradd --system --gid cachegate cachegate

# Copy installed packages from builder
COPY --from=builder /install /usr/local

WORKDIR /app

# Copy application source
COPY src/ ./src/
COPY config/ ./config/

# Own everything as cachegate user
RUN chown -R cachegate:cachegate /app

USER cachegate

# Expose app port
EXPOSE 8000

# Health check (relies on /healthz endpoint)
HEALTHCHECK --interval=15s --timeout=5s --start-period=10s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/healthz')" \
    || exit 1

# Run the ASGI app via uvicorn
CMD ["python", "-m", "uvicorn", "cachegate.main:app", \
     "--host", "0.0.0.0", "--port", "8000", \
     "--workers", "1", "--log-level", "info"]

ENV PYTHONPATH=/app/src
