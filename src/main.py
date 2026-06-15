from __future__ import annotations

import asyncio
import time
from contextlib import asynccontextmanager

import httpx
import uvicorn
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from src.config import settings
from src.middleware.circuit_breaker import VramCircuitBreakerMiddleware
from src.monitor.vram import gpu_status, start_monitor, stop_monitor
from src.queue.worker import queue_consumer, queue_status
from src.router.dispatch import router as dispatch_router


# ---------------------------------------------------------------------------
# Startup warmup
# ---------------------------------------------------------------------------

async def _probe_upstream(client: httpx.AsyncClient) -> None:
    """
    Sends a minimal single-token request to the upstream LLM to:
      1. Verify the upstream is reachable (health probe).
      2. Force the model into VRAM so the first real request is not cold.

    Retry policy:
      - Exponential backoff: delay = min(base * 2^attempt, total_timeout / 2)
      - Maximum `warmup_max_retries` attempts within `warmup_total_timeout` seconds.
      - On final failure, logs a warning and returns — never raises.
        The gateway continues to start normally; warmup failure is not fatal.
    """
    url = f"{settings.llm_base_url.rstrip('/')}/chat/completions"
    headers = {
        "Authorization": f"Bearer {settings.llm_api_key}",
        "Content-Type": "application/json",
    }
    # Minimal payload: one token of output is enough to force model loading.
    payload = {
        "model": settings.llm_default_model,
        "messages": [{"role": "user", "content": "hi"}],
        "max_tokens": 1,
        "stream": False,
    }

    for attempt in range(settings.warmup_max_retries):
        try:
            t0 = time.monotonic()
            resp = await client.post(url, json=payload, headers=headers)
            resp.raise_for_status()
            elapsed = time.monotonic() - t0
            print(
                f"[Warmup] ✅ Model '{settings.llm_default_model}' loaded into VRAM "
                f"({elapsed:.2f}s, attempt {attempt + 1}/{settings.warmup_max_retries})"
            )
            return  # success

        except Exception as exc:
            remaining_attempts = settings.warmup_max_retries - attempt - 1
            if remaining_attempts == 0:
                # Final attempt failed — log and give up gracefully
                print(
                    f"[Warmup] ❌ All {settings.warmup_max_retries} probe attempts failed. "
                    f"Last error: {type(exc).__name__}: {exc}. "
                    "Gateway will start anyway — model may have a cold start on first request."
                )
                return

            # Exponential backoff capped at half the total timeout budget
            delay = min(
                settings.warmup_retry_base_delay * (2 ** attempt),
                settings.warmup_total_timeout / 2,
            )
            print(
                f"[Warmup] ⚠️  Probe attempt {attempt + 1}/{settings.warmup_max_retries} failed "
                f"({type(exc).__name__}). Retrying in {delay:.1f}s..."
            )
            await asyncio.sleep(delay)


async def _run_warmup(client: httpx.AsyncClient) -> None:
    """
    Wraps _probe_upstream in an overall timeout guard.
    If the total warmup budget is exceeded (e.g. Ollama is completely offline),
    the coroutine is cancelled and a warning is printed.
    """
    if not settings.warmup_enabled:
        print("[Warmup] Skipped (warmup_enabled=False)")
        return

    print(
        f"[Warmup] 🔥 Probing upstream '{settings.llm_default_model}' "
        f"(timeout={settings.warmup_total_timeout:.0f}s, "
        f"max_retries={settings.warmup_max_retries})..."
    )
    try:
        await asyncio.wait_for(
            _probe_upstream(client),
            timeout=settings.warmup_total_timeout,
        )
    except asyncio.TimeoutError:
        print(
            f"[Warmup] ❌ Probe timed out after {settings.warmup_total_timeout:.0f}s. "
            "Gateway starting without VRAM pre-heat."
        )


# ---------------------------------------------------------------------------
# Application lifespan
# ---------------------------------------------------------------------------

@asynccontextmanager
async def lifespan(app: FastAPI):
    """
    Manages the full application lifecycle in three phases:

    Startup
    ───────
    1. Create the global HTTP connection pool (shared by all routes and workers).
    2. Run the upstream warmup probe to verify connectivity and pre-load VRAM.
       Warmup is non-blocking: even if it fails the gateway opens for traffic.
    3. Spawn N background queue consumer workers.

    Shutdown
    ────────
    4. Cancel all workers and await their clean exit.
    5. Close the HTTP connection pool.
    """
    # Phase 1 — Connection pool
    pool_limits = httpx.Limits(
        max_connections=settings.pool_max_connections,
        max_keepalive_connections=settings.pool_max_keepalive,
    )
    timeout = httpx.Timeout(
        connect=settings.pool_connect_timeout,
        read=settings.pool_read_timeout,
        write=settings.pool_write_timeout,
        pool=settings.pool_connect_timeout,
    )
    client = httpx.AsyncClient(
        limits=pool_limits,
        timeout=timeout,
        http2=True,
        follow_redirects=True,
    )
    app.state.http_client = client
    print(f"[Lifespan] 🚀 Connection pool ready (max_conn={settings.pool_max_connections}, http2=True)")

    # Phase 2 — VRAM monitor (non-blocking background probe)
    await start_monitor()

    # Phase 3 — Warmup probe (non-blocking: launched as a background task so
    # the gateway starts serving traffic while the probe retries in the background)
    warmup_task = asyncio.create_task(_run_warmup(client))
    app.state.warmup_task = warmup_task

    # Phase 4 — Queue workers
    workers: list[asyncio.Task] = []
    for i in range(settings.queue_worker_count):
        t = asyncio.create_task(queue_consumer(client, worker_id=i))
        workers.append(t)
    print(
        f"[Lifespan] 🏭 {settings.queue_worker_count} queue workers launched "
        f"(max_queue={settings.queue_max_size})"
    )

    yield  # ── gateway is live ──

    # Phase 5 — Cancel workers
    for t in workers:
        t.cancel()
    await asyncio.gather(*workers, return_exceptions=True)
    print("[Lifespan] 🛑 All queue workers stopped")

    # Cancel warmup if still running (e.g. gateway stopped almost immediately)
    if not warmup_task.done():
        warmup_task.cancel()
        try:
            await warmup_task
        except asyncio.CancelledError:
            pass

    # Phase 6 — VRAM monitor teardown (releases NVML C driver handle)
    await stop_monitor()

    # Phase 7 — Connection pool teardown
    await client.aclose()
    print("[Lifespan] 🛑 Connection pool released")


# ---------------------------------------------------------------------------
# FastAPI application
# ---------------------------------------------------------------------------

app = FastAPI(
    title=settings.app_name,
    version="0.2.0",
    lifespan=lifespan,
)

# Middleware stack — execution order is bottom-to-top:
# 1. CORS headers (outermost)
# 2. VRAM circuit breaker (fires before body parsing)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.add_middleware(VramCircuitBreakerMiddleware)

app.include_router(dispatch_router)


# ---------------------------------------------------------------------------
# Global exception handlers
# ---------------------------------------------------------------------------

@app.exception_handler(httpx.RequestError)
async def upstream_connection_error_handler(request: Request, exc: httpx.RequestError):
    """Handles connection errors to the upstream LLM provider."""
    return JSONResponse(
        status_code=503,
        content={
            "error": {
                "message": f"Failed to connect to upstream LLM provider: {str(exc)}",
                "type": "server_error",
                "param": None,
                "code": "upstream_connection_failed",
            }
        },
    )


@app.exception_handler(httpx.HTTPStatusError)
async def upstream_status_error_handler(request: Request, exc: httpx.HTTPStatusError):
    """Handles HTTP errors returned by the upstream LLM provider."""
    status_code = exc.response.status_code

    try:
        upstream_error = exc.response.json()
        if "error" in upstream_error:
            return JSONResponse(status_code=status_code, content=upstream_error)
    except Exception:
        pass

    error_msg = exc.response.text or str(exc)
    return JSONResponse(
        status_code=status_code,
        content={
            "error": {
                "message": f"Upstream LLM error: {error_msg}",
                "type": "api_error",
                "param": None,
                "code": "upstream_api_error",
            }
        },
    )


# ---------------------------------------------------------------------------
# Utility routes
# ---------------------------------------------------------------------------

@app.get("/health")
async def health_check():
    """Returns gateway liveness, queue utilisation, and GPU VRAM metrics."""
    warmup_task: asyncio.Task | None = getattr(app.state, "warmup_task", None)
    warmup_status = (
        "complete" if (warmup_task and warmup_task.done()) else "in_progress"
        if warmup_task else "disabled"
    )
    snap = gpu_status()
    gpu_info = {
        "available": snap.available,
        "zone": snap.zone.value,
        "usage_pct": snap.usage_pct,
        "used_mb": snap.used_mb,
        "total_mb": snap.total_mb,
    }
    return {
        "status": "healthy",
        "queue": queue_status(),
        "gpu": gpu_info,
        "warmup": warmup_status,
    }


if __name__ == "__main__":
    uvicorn.run(
        "src.main:app",
        host="0.0.0.0",
        port=8000,
        reload=settings.debug,
    )
