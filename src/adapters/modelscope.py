from __future__ import annotations

from typing import Any

from src.adapters.base import BaseLLMAdapter
from src.config import settings


class ModelScopeAdapter(BaseLLMAdapter):
    """
    Adapter for ModelScope/DashScope (Alibaba Qwen family and friends).

    DashScope uses a different wire format than OpenAI:
    - Request:  messages are nested under `input.messages`; generation params go to `parameters`.
    - Response: choices live under `output.choices`; token counts use different field names.

    This adapter bridges both directions so the rest of the gateway stays format-agnostic.

    Supported model prefix: 'ms:' or 'modelscope:'
    Example: "ms:qwen-vl-plus"  →  model name sent to DashScope: "qwen-vl-plus"
    """

    @property
    def name(self) -> str:
        return "modelscope"

    def get_endpoint(self, stream: bool) -> str:
        base = settings.modelscope_base_url.rstrip("/")
        return f"{base}/services/aigc/text-generation/generation"

    def get_headers(self, stream: bool) -> dict[str, str]:
        headers = {
            "Authorization": f"Bearer {settings.modelscope_api_key}",
            "Content-Type": "application/json",
        }
        if stream:
            # DashScope activates SSE via a custom header rather than Accept
            headers["X-DashScope-SSE"] = "enable"
        return headers

    def build_payload(self, generic_payload: dict[str, Any]) -> dict[str, Any]:
        """OpenAI format → DashScope format."""
        model: str = generic_payload.get("model", "")
        # Strip adapter prefix: "ms:qwen-vl-plus" → "qwen-vl-plus"
        if ":" in model:
            model = model.split(":", 1)[1]

        parameters: dict[str, Any] = {}
        if (temp := generic_payload.get("temperature")) is not None:
            parameters["temperature"] = temp
        if (max_tok := generic_payload.get("max_tokens")) is not None:
            parameters["max_tokens"] = max_tok
        if (top_p := generic_payload.get("top_p")) is not None:
            parameters["top_p"] = top_p
        if generic_payload.get("stream"):
            parameters["incremental_output"] = True  # DashScope incremental SSE mode

        return {
            "model": model,
            "input": {
                "messages": generic_payload.get("messages", []),
            },
            "parameters": parameters,
        }

    def parse_response(self, response_json: dict[str, Any]) -> dict[str, Any]:
        """DashScope format → OpenAI format."""
        output = response_json.get("output", {})
        choices = output.get("choices", [])
        usage = response_json.get("usage", {})

        return {
            "id": response_json.get("request_id", ""),
            "object": "chat.completion",
            "choices": [
                {
                    "index": i,
                    "message": choice.get("message", {}),
                    "finish_reason": choice.get("finish_reason", "stop"),
                }
                for i, choice in enumerate(choices)
            ],
            "usage": {
                "prompt_tokens": usage.get("input_tokens", 0),
                "completion_tokens": usage.get("output_tokens", 0),
                "total_tokens": usage.get("total_tokens", 0),
            },
        }
