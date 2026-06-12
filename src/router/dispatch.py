from __future__ import annotations

import asyncio
from typing import Any, AsyncIterator

import httpx
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, StreamingResponse

from src.adapters.registry import get_adapter
from src.config import settings
from src.media.offload import offload_images
from src.queue.worker import RequestTask, request_queue
from src.router.schemas import ChatCompletionRequest

router = APIRouter(prefix="/v1", tags=["LLM Dispatch"])


# ---------------------------------------------------------------------------
# Utilities
# ---------------------------------------------------------------------------

def get_http_client(request: Request) -> httpx.AsyncClient:
    """Retrieves the globally shared HTTP connection pool instance."""
    return request.app.state.http_client


def build_upstream_payload(body: ChatCompletionRequest) -> dict[str, Any]:
    """
    Constructs a generic OpenAI-format payload from the validated request body.
    Model name is kept as-is (including any adapter prefix); the adapter's
    build_payload() will strip the prefix when forwarding to the backend.
    """
    payload: dict[str, Any] = {
        "model": body.model or settings.llm_default_model,
        "messages": [msg.model_dump() for msg in body.messages],
        "stream": body.stream,
    }

    if body.temperature is not None:
        payload["temperature"] = body.temperature
    if body.max_tokens is not None:
        payload["max_tokens"] = body.max_tokens
    if body.top_p is not None:
        payload["top_p"] = body.top_p

    if body.think is not None:
        payload["think"] = body.think
    elif settings.ollama_disable_think:
        payload["think"] = False

    return payload


def _queue_full_response() -> JSONResponse:
    """Standard 429 response when the request queue is saturated."""
    return JSONResponse(
        status_code=429,
        content={
            "error": {
                "message": (
                    f"Server overloaded. Request queue is full "
                    f"({request_queue.maxsize}/{request_queue.maxsize}). "
                    "Please retry later."
                ),
                "type": "server_error",
                "param": None,
                "code": "queue_full",
            }
        },
    )


# ---------------------------------------------------------------------------
# Disconnect watcher
# ---------------------------------------------------------------------------

async def _watch_disconnect(request: Request, cancel_event: asyncio.Event) -> None:
    """
    Polls for client disconnect and signals cancel_event when detected.
    Exits as soon as the event is set (either by this watcher or externally).
    """
    while not cancel_event.is_set():
        if await request.is_disconnected():
            print("[Route] Client disconnected — signalling worker to cancel")
            cancel_event.set()
            return
        await asyncio.sleep(0.3)


# ---------------------------------------------------------------------------
# Streaming consumer generator
# ---------------------------------------------------------------------------

async def _stream_from_queue(task: RequestTask, request: Request) -> AsyncIterator[bytes]:
    """
    Drains raw bytes from the task's chunk_queue until the None sentinel.

    Runs a disconnect watcher concurrently. If the client drops, the watcher
    sets cancel_event, the worker stops generating, and this generator exits.
    """
    watcher = asyncio.create_task(_watch_disconnect(request, task.cancel_event))

    try:
        while True:
            get_task = asyncio.create_task(task.chunk_queue.get())
            cancel_task = asyncio.create_task(task.cancel_event.wait())

            done, pending = await asyncio.wait(
                {get_task, cancel_task},
                return_when=asyncio.FIRST_COMPLETED,
            )

            for t in pending:
                t.cancel()

            if cancel_task in done:
                break  # client disconnected

            chunk = get_task.result()
            if chunk is None:
                break  # sentinel: stream finished normally

            yield chunk

    finally:
        watcher.cancel()
        try:
            await watcher
        except asyncio.CancelledError:
            pass


# ---------------------------------------------------------------------------
# Route
# ---------------------------------------------------------------------------

@router.post("/chat/completions")
async def chat_completions(body: ChatCompletionRequest, request: Request):
    """
    Unified LLM routing gateway.

    Per-request pipeline:
    1. Build a generic OpenAI-format payload.
    2. Resolve the backend adapter via the routing strategy engine.
    3. Offload any large base64 images to temp files (keeps the queue lean).
    4. Enqueue the task — 429 immediately if the queue is full (backpressure).
    5a. Streaming:     return a StreamingResponse backed by the task's chunk_queue.
    5b. Non-streaming: await result_future, racing against client disconnect.
    """
    payload = build_upstream_payload(body)

    # Step 2 — Adapter resolution (prefix → config table → default)
    adapter = get_adapter(payload["model"])

    # Step 3 — Image offloading: strip large base64 blobs, replace with imgref: pointers
    payload, had_offloads = offload_images(payload)

    # Step 4 — Enqueue
    task = RequestTask(
        payload=payload,
        adapter=adapter,
        stream=body.stream,
        has_offloaded_images=had_offloads,
    )

    try:
        request_queue.put_nowait(task)
    except asyncio.QueueFull:
        if had_offloads:
            # Release temp files immediately — task will never be processed
            from src.media.offload import release_images
            release_images(payload)
        return _queue_full_response()

    # Step 5a — Streaming
    if body.stream:
        return StreamingResponse(
            _stream_from_queue(task, request),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
                "X-Accel-Buffering": "no",
            },
        )

    # Step 5b — Non-streaming: race result future against client disconnect
    disconnect_task = asyncio.create_task(_watch_disconnect(request, task.cancel_event))
    cancel_wait_task = asyncio.create_task(task.cancel_event.wait())

    try:
        done, pending = await asyncio.wait(
            {task.result_future, cancel_wait_task},
            return_when=asyncio.FIRST_COMPLETED,
        )

        for t in pending:
            t.cancel()

        if task.cancel_event.is_set() and not task.result_future.done():
            return JSONResponse(status_code=499, content={"detail": "Client closed request"})

        result = await task.result_future
        return result

    except httpx.HTTPStatusError as exc:
        raise exc
    except httpx.RequestError as exc:
        raise exc
    finally:
        task.cancel_event.set()
        disconnect_task.cancel()
        cancel_wait_task.cancel()
        try:
            await disconnect_task
        except asyncio.CancelledError:
            pass
