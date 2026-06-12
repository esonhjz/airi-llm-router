from __future__ import annotations

from src.adapters.base import BaseLLMAdapter
from src.adapters.modelscope import ModelScopeAdapter
from src.adapters.ollama import OllamaAdapter
from src.config import settings

# ---------------------------------------------------------------------------
# Singleton adapter instances — stateless, safe to share across all workers.
# ---------------------------------------------------------------------------

_ADAPTERS: dict[str, BaseLLMAdapter] = {
    "ollama": OllamaAdapter(),
    "modelscope": ModelScopeAdapter(),
}

# Model-name prefix → adapter name (checked before config routes)
_PREFIX_MAP: dict[str, str] = {
    "ollama": "ollama",
    "ms": "modelscope",
    "modelscope": "modelscope",
}


def get_adapter(model_name: str) -> BaseLLMAdapter:
    """
    Routing strategy engine — resolves the correct adapter for a model name.

    Resolution order (first match wins):
    1. Explicit prefix in model name: "ms:qwen-vl-plus" → modelscope
    2. Config-defined model_routes substring match: {"qwen-vl": "modelscope"}
    3. settings.default_adapter fallback (default: "ollama")

    The model name itself is NOT modified here; prefix stripping is the
    responsibility of each adapter's build_payload() method.
    """
    # 1. Prefix routing
    if ":" in model_name:
        prefix = model_name.split(":", 1)[0].lower()
        adapter_name = _PREFIX_MAP.get(prefix)
        if adapter_name and adapter_name in _ADAPTERS:
            return _ADAPTERS[adapter_name]

    # 2. Config routing table (substring match, evaluated in insertion order)
    for pattern, adapter_name in settings.model_routes.items():
        if pattern.lower() in model_name.lower() and adapter_name in _ADAPTERS:
            return _ADAPTERS[adapter_name]

    # 3. Default fallback
    default = settings.default_adapter
    return _ADAPTERS.get(default, _ADAPTERS["ollama"])


__all__ = ["BaseLLMAdapter", "get_adapter"]
