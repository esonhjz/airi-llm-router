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
        # Only strip the adapter routing prefix "ollama:xxx".
        # Ollama's own model:tag format (e.g. "qwen2.5:7b") must be preserved —
        # the colon there is a version separator, not a routing prefix.
        model: str = payload.get("model", "")
        if model.startswith("ollama:"):
            payload["model"] = model[len("ollama:"):]
        return payload
