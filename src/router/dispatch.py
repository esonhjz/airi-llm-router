from __future__ import annotations

import json
from typing import Any

import httpx
from fastapi import APIRouter, Request
from fastapi.responses import StreamingResponse

from src.config import settings
from src.router.schemas import ChatCompletionRequest

router = APIRouter(prefix="/v1", tags=["LLM Dispatch"])

def get_http_client(request: Request) -> httpx.AsyncClient:
    """Retrieves the globally shared HTTP connection pool instance."""
    return request.app.state.http_client

def build_upstream_payload(body: ChatCompletionRequest) -> dict[str, Any]:
    """Constructs the request payload for the upstream LLM."""
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

async def _stream_upstream(client: httpx.AsyncClient, payload: dict[str, Any]):
    """Handles the SSE streaming response from the LLM."""
    headers = {
        "Authorization": f"Bearer {settings.llm_api_key}",
        "Content-Type": "application/json",
        "Accept": "text/event-stream",
    }
    url = f"{settings.llm_base_url.rstrip('/')}/chat/completions"

    try:
        async with client.stream("POST", url, json=payload, headers=headers) as response:
            response.raise_for_status()
            async for line in response.aiter_lines():
                if line.strip():
                    yield f"{line}\n\n"
    except httpx.HTTPError as exc:
        if isinstance(exc, httpx.HTTPStatusError):
            try:
                upstream_error = exc.response.json()
                if "error" in upstream_error:
                    yield f"{json.dumps(upstream_error)}\n\n"
                    return
            except Exception:
                pass
            error_msg = exc.response.text or str(exc)
        else:
            error_msg = f"Connection failed: {str(exc)}"
            
        fallback_error = {
            "error": {
                "message": f"Upstream LLM error: {error_msg}",
                "type": "api_error",
                "param": None,
                "code": "upstream_api_error"
            }
        }
        yield f"{json.dumps(fallback_error)}\n\n"

@router.post("/chat/completions")
async def chat_completions(body: ChatCompletionRequest, request: Request):
    """
    Unified LLM routing gateway.
    Supports streaming (SSE), non-streaming, and multi-modal requests.
    """
    client = get_http_client(request)
    payload = build_upstream_payload(body)

    if body.stream:
        return StreamingResponse(
            _stream_upstream(client, payload),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
                "X-Accel-Buffering": "no",
            },
        )

    headers = {
        "Authorization": f"Bearer {settings.llm_api_key}",
        "Content-Type": "application/json",
    }
    url = f"{settings.llm_base_url.rstrip('/')}/chat/completions"

    upstream_resp = await client.post(url, json=payload, headers=headers)
    upstream_resp.raise_for_status()

    return upstream_resp.json()
