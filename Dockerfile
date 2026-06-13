# ─────────────────────────────────────────────────────────────────────────────
# Stage 1: builder
# Install dependencies into an isolated prefix so the final image only copies
# the pre-built wheels — no compiler toolchain, no pip cache, no .git history.
# ─────────────────────────────────────────────────────────────────────────────
FROM python:3.11-slim AS builder

WORKDIR /build

# Copy only the dependency manifest first so Docker can cache this layer
# independently of source-code changes.  Rebuilds only rerun pip when
# pyproject.toml actually changes.
COPY pyproject.toml .

RUN pip install --upgrade pip \
    && pip install --prefix=/install . \
    && pip install --prefix=/install uvicorn[standard]


# ─────────────────────────────────────────────────────────────────────────────
# Stage 2: runtime
# Lean, non-root image.  No build tools, no cache, minimal attack surface.
# ─────────────────────────────────────────────────────────────────────────────
FROM python:3.11-slim AS runtime

# Run as an unprivileged user for basic container hardening.
RUN addgroup --system airi && adduser --system --ingroup airi airi

WORKDIR /app

# Pull in the pre-built site-packages from the builder stage.
COPY --from=builder /install /usr/local

# Copy application source — this layer changes on every code push, so it is
# intentionally placed last to maximise cache reuse above.
COPY src/ ./src/

# Switch to non-root before any network-facing process starts.
USER airi

# Expose the gateway port.  docker-compose / K8s should map this externally.
EXPOSE 8000

# PYTHONUNBUFFERED: guarantees log lines are flushed immediately to stdout.
# PYTHONDONTWRITEBYTECODE: avoids cluttering the container with .pyc files.
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

# Health check: the /health endpoint is fast and dependency-free.
HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/health')"

CMD ["uvicorn", "src.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1"]
