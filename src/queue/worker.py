from __future__ import annotations

import asyncio
import json
import random
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

import httpx

from src.config import settings
from src.media.offload import release_images, restore_images

if TYPE_CHECKING:
    from src.adapters.base import BaseLLMAdapter


# ---------------------------------------------------------------------------
# Task model
# ---------------------------------------------------------------------------

@dataclass
class RequestTask:
    """
    Carries a single LLM request through the global queue.

    payload              — OpenAI-format dict; may contain 'imgref:' pointers
                           if images were offloaded by the route layer.
    adapter              — Backend adapter resolved at dispatch time.
    stream               — True for SSE streaming, False for JSON response.
    has_offloaded_images — Whether payload contains imgref: pointers.
                           If True, workers call restore_images() before forwarding
                           and release_images() in their finally block.
    result_future        — Resolved by the worker for non-streaming requests.
    chunk_queue          — Fed by the worker for streaming requests; None is sentinel.
    cancel_event         — Set by the route layer on client disconnect.
    """
    payload: dict[str, Any]
    adapter: "BaseLLMAdapter"
    stream: bool = False
    has_offloaded_images: bool = False

    result_future: asyncio.Future = field(
        default_factory=lambda: asyncio.get_running_loop().create_future()
    )
    chunk_queue: asyncio.Queue[bytes | None] = field(
        default_factory=lambda: asyncio.Queue(maxsize=256)
    )
    cancel_event: asyncio.Event = field(
        default_factory=asyncio.Event
    )


# ---------------------------------------------------------------------------
# Global bounded queue — top-level backpressure valve.
# ---------------------------------------------------------------------------

request_queue: asyncio.Queue[RequestTask] = asyncio.Queue(maxsize=settings.queue_max_size)


# ---------------------------------------------------------------------------
# Retry policy
# ---------------------------------------------------------------------------

_RETRYABLE_STATUS_CODES: frozenset[int] = frozenset({429, 502, 503, 504})

_RETRYABLE_EXCEPTIONS: tuple[type[Exception], ...] = (
    httpx.ConnectError,
    httpx.ConnectTimeout,
    httpx.ReadTimeout,
    httpx.RemoteProtocolError,
)


def _is_retryable(exc: Exception) -> bool:
    if isinstance(exc, _RETRYABLE_EXCEPTIONS):
        return True
    if isinstance(exc, httpx.HTTPStatusError):
        return exc.response.status_code in _RETRYABLE_STATUS_CODES
    return False


def _retry_after_seconds(exc: Exception) -> float | None:
    """Extracts Retry-After header value from a 429 response, if present."""
    if isinstance(exc, httpx.HTTPStatusError) and exc.response.status_code == 429:
        header = exc.response.headers.get("retry-after", "")
        if header.isdigit():
            return float(header)
    return None


async def _backoff(attempt: int, exc: Exception) -> None:
    """Exponential backoff with jitter; respects upstream Retry-After hints."""
    server_hint = _retry_after_seconds(exc)
    if server_hint is not None:
        delay = min(server_hint, settings.upstream_retry_max_delay)
    else:
        delay = min(
            settings.upstream_retry_base_delay * (2 ** attempt) + random.uniform(0.0, 0.5),
            settings.upstream_retry_max_delay,
        )
    print(
        f"[Retry] Attempt {attempt + 1}/{settings.upstream_max_retries} — "
        f"backing off {delay:.2f}s after {type(exc).__name__}"
    )
    await asyncio.sleep(delay)


# ---------------------------------------------------------------------------
# Error formatting
# ---------------------------------------------------------------------------

def _make_error_chunk(exc: Exception) -> bytes:
    """Converts any exception into an OpenAI-format JSON error block (bytes)."""
    if isinstance(exc, httpx.HTTPStatusError):
        try:
            upstream = exc.response.json()
            if "error" in upstream:
                return json.dumps(upstream).encode()
        except Exception:
            pass
        error_msg = exc.response.text or str(exc)
        code = "upstream_api_error"
    elif isinstance(exc, httpx.HTTPError):
        error_msg = f"Connection failed: {exc}"
        code = "upstream_connection_failed"
    else:
        error_msg = str(exc)
        code = "internal_error"

    return json.dumps({
        "error": {
            "message": f"Upstream LLM error: {error_msg}",
            "type": "api_error",
            "param": None,
            "code": code,
        }
    }).encode()


# ---------------------------------------------------------------------------
# Executors
# ---------------------------------------------------------------------------

async def _execute_non_stream(client: httpx.AsyncClient, task: RequestTask) -> None:
    """
    Forwards a non-streaming request to the upstream LLM with retry support.

    Flow per attempt:
      1. Check cancel_event — abort immediately if client disconnected.
      2. Restore offloaded images into a fresh payload copy.
      3. Ask the adapter to transform to backend wire format.
      4. Race the HTTP POST against cancel_event.
      5. On transient error: back off and retry.
      6. On success: ask adapter to normalise response, resolve future.
      7. On final failure: set exception on future for the route handler.
    """
    last_exc: Exception | None = None

    for attempt in range(settings.upstream_max_retries + 1):
        if task.cancel_event.is_set():
            print("[Worker] Non-stream cancelled before attempt")
            if not task.result_future.done():
                task.result_future.cancel()
            return

        # Reconstruct full payload (re-reads temp files on every retry attempt)
        live_payload = (
            restore_images(task.payload) if task.has_offloaded_images else task.payload
        )
        wire_payload = task.adapter.build_payload(live_payload)
        url = task.adapter.get_endpoint(stream=False)
        headers = task.adapter.get_headers(stream=False)

        http_task = asyncio.create_task(client.post(url, json=wire_payload, headers=headers))
        cancel_task = asyncio.create_task(task.cancel_event.wait())

        done, pending = await asyncio.wait(
            {http_task, cancel_task},
            return_when=asyncio.FIRST_COMPLETED,
        )

        for t in pending:
            t.cancel()
            try:
                await t
            except (asyncio.CancelledError, Exception):
                pass

        if cancel_task in done:
            print("[Worker] Non-stream cancelled by client disconnect")
            if not task.result_future.done():
                task.result_future.cancel()
            return

        try:
            resp = http_task.result()
            resp.raise_for_status()
            result = task.adapter.parse_response(resp.json())
            task.result_future.set_result(result)
            return
        except Exception as exc:
            last_exc = exc
            if attempt < settings.upstream_max_retries and _is_retryable(exc):
                await _backoff(attempt, exc)
                continue
            break

    if last_exc is not None and not task.result_future.done():
        task.result_future.set_exception(last_exc)


async def _execute_stream(client: httpx.AsyncClient, task: RequestTask) -> None:
    """
    Opens a streaming connection to the upstream LLM with retry support.

    Retry semantics:
      - A retry is only safe if zero chunks have reached chunk_queue yet.
        Once data is flowing, retrying would corrupt the SSE frame sequence.
      - chunks_sent tracks this; checked before deciding to retry.

    Image restoration:
      - restore_images() is called on each attempt (temp files persist until
        the outer finally calls release_images()).

    Cancellation:
      - cancel_event is checked between every chunk; the loop breaks and the
        upstream TCP connection is closed by the context manager exit.
    """
    for attempt in range(settings.upstream_max_retries + 1):
        if task.cancel_event.is_set():
            await task.chunk_queue.put(None)
            return

        live_payload = (
            restore_images(task.payload) if task.has_offloaded_images else task.payload
        )
        wire_payload = task.adapter.build_payload(live_payload)
        url = task.adapter.get_endpoint(stream=True)
        headers = task.adapter.get_headers(stream=True)

        error_bytes: bytes | None = None
        chunks_sent: int = 0

        try:
            async with client.stream("POST", url, json=wire_payload, headers=headers) as response:
                response.raise_for_status()
                # Past raise_for_status — committed to this attempt, no more retries

                async for raw_chunk in response.aiter_raw():
                    if task.cancel_event.is_set():
                        print("[Worker] Stream cancelled — closing upstream connection")
                        break
                    if raw_chunk:
                        await task.chunk_queue.put(raw_chunk)
                        chunks_sent += 1

        except httpx.HTTPError as exc:
            if chunks_sent == 0 and attempt < settings.upstream_max_retries and _is_retryable(exc):
                await _backoff(attempt, exc)
                continue  # retry — no data sent yet, safe to start fresh
            error_bytes = _make_error_chunk(exc)

        finally:
            if error_bytes is not None:
                await task.chunk_queue.put(error_bytes)
            # Send sentinel only on final exit (not when continuing to next retry)
            if error_bytes is not None or chunks_sent > 0 or task.cancel_event.is_set():
                await task.chunk_queue.put(None)
                return

    # Retry loop exhausted without success
    await task.chunk_queue.put(None)


# ---------------------------------------------------------------------------
# Consumer worker
# ---------------------------------------------------------------------------

async def queue_consumer(client: httpx.AsyncClient, worker_id: int) -> None:
    """
    Long-running consumer loop.

    Serialises one request at a time, limiting true upstream concurrency to
    queue_worker_count. Handles image cleanup in a finally block so temp files
    are always released regardless of success, failure, or cancellation.
    """
    print(f"[Queue] Worker-{worker_id} started")
    try:
        while True:
            task = await request_queue.get()
            try:
                if task.stream:
                    await _execute_stream(client, task)
                else:
                    await _execute_non_stream(client, task)
            finally:
                if task.has_offloaded_images:
                    release_images(task.payload)
                request_queue.task_done()
    except asyncio.CancelledError:
        print(f"[Queue] Worker-{worker_id} shutting down")


# ---------------------------------------------------------------------------
# Observability
# ---------------------------------------------------------------------------

def queue_status() -> dict[str, Any]:
    """Returns a real-time snapshot of global queue metrics."""
    size = request_queue.qsize()
    max_size = request_queue.maxsize
    return {
        "current_size": size,
        "max_size": max_size,
        "utilization": f"{size / max_size * 100:.1f}%",
    }
