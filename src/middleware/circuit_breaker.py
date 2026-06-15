"""VRAM-aware circuit breaker middleware.

Sits in front of ALL request handlers.  On every inbound request it reads the
latest GPU snapshot (O(1), no I/O) and enforces the three-zone policy:

  SAFE    → pass through immediately.
  WARNING → pass through, but inject X-VRAM-Status header as a soft signal.
  DANGER  → hard reject with 429, Retry-After, and X-VRAM-Status: Critical.

This middleware is the absolute first line of defence; it fires BEFORE the
request body is even parsed by Pydantic, so zero compute is wasted on
requests that would be rejected anyway.
"""

from __future__ import annotations

import json

from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import Response, JSONResponse

from src.monitor.vram import gpu_status, retry_after_hint, VramZone
from src.logger import log_event


class VramCircuitBreakerMiddleware(BaseHTTPMiddleware):
    """Starlette middleware that enforces GPU-pressure-based load shedding."""

    # Paths exempt from circuit breaking (health probes must always respond).
    _EXEMPT_PATHS: frozenset[str] = frozenset({"/health", "/docs", "/openapi.json", "/redoc"})

    async def dispatch(
        self, request: Request, call_next: RequestResponseEndpoint
    ) -> Response:
        # Skip non-API routes (health checks, docs) so monitoring stays alive.
        if request.url.path in self._EXEMPT_PATHS:
            return await call_next(request)

        snap = gpu_status()

        # ── DANGER zone: hard circuit-break ─────────────────────────────────
        if snap.zone == VramZone.DANGER:
            retry = retry_after_hint()
            
            # Emit structured JSON log
            log_event(
                event_type="hard_throttle",
                target=request.url.path,
                client_ip=request.client.host if request.client else "unknown",
                vram_percent=snap.usage_pct,
                msg="Circuit broken due to DANGER VRAM levels"
            )

            body = {
                "error": {
                    "message": (
                        f"GPU VRAM critically high ({snap.usage_pct}% of "
                        f"{snap.total_mb:.0f} MB used). "
                        "Request rejected to prevent OOM. "
                        f"Retry after ~{retry}s."
                    ),
                    "type": "server_error",
                    "param": None,
                    "code": "vram_circuit_open",
                }
            }
            return JSONResponse(
                status_code=429,
                content=body,
                headers={
                    "Retry-After": str(retry),
                    "X-VRAM-Status": "Critical",
                    "X-VRAM-Usage": f"{snap.usage_pct}%",
                },
            )

        # ── SAFE or WARNING zone: let the request through ───────────────────
        response = await call_next(request)

        # Inject advisory headers so smart clients can preemptively throttle.
        if snap.available:
            status_label = "Warning" if snap.zone == VramZone.WARNING else "OK"
            response.headers["X-VRAM-Status"] = status_label
            response.headers["X-VRAM-Usage"] = f"{snap.usage_pct}%"

        return response
