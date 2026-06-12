from __future__ import annotations

from typing import Any

from src.adapters.base import BaseLLMAdapter
from src.config import settings


class OllamaAdapter(BaseLLMAdapter):
    """
    Adapter for Ollama (and any OpenAI-compatible backend).

    The wire format is already OpenAI-compatible so almost no transformation is
    needed — the only work is stripping the optional 'ollama:' model prefix.
    """

    @property
    def name(self) -> str:
        return "ollama"

    def get_endpoint(self, stream: bool) -> str:
        return f"{settings.llm_base_url.rstrip('/')}/chat/completions"

    def get_headers(self, stream: bool) -> dict[str, str]:
        headers = {
            "Authorization": f"Bearer {settings.llm_api_key}",
            "Content-Type": "application/json",
        }
        if stream:
            headers["Accept"] = "text/event-stream"
        return headers

    def build_payload(self, generic_payload: dict[str, Any]) -> dict[str, Any]:
        payload = dict(generic_payload)
        # Strip adapter prefix: "ollama:qwen2.5" → "qwen2.5"
        model: str = payload.get("model", "")
        if ":" in model:
            payload["model"] = model.split(":", 1)[1]
        return payload
