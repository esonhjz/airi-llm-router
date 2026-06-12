# src/adapters/base.py
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any


class BaseLLMAdapter(ABC):
    """
    Abstract base for all upstream LLM backend adapters.

    Each adapter is responsible for:
    - Providing the correct request URL and HTTP headers for its backend.
    - Transforming the generic OpenAI-format payload into the backend wire format.
    - Normalising non-streaming responses back to OpenAI format.

    Adapters are stateless singletons; a single instance is shared across all workers.
    """

    @property
    @abstractmethod
    def name(self) -> str:
        """Unique backend identifier, e.g. 'ollama' or 'modelscope'."""

    @abstractmethod
    def get_endpoint(self, stream: bool) -> str:
        """Returns the full request URL for this backend."""

    @abstractmethod
    def get_headers(self, stream: bool) -> dict[str, str]:
        """Returns the HTTP headers required by this backend."""

    @abstractmethod
    def build_payload(self, generic_payload: dict[str, Any]) -> dict[str, Any]:
        """
        Transforms the generic OpenAI-format payload into the backend wire format.
        Implementations should also strip any adapter prefix from the model name.
        """

    def parse_response(self, response_json: dict[str, Any]) -> dict[str, Any]:
        """
        Normalises a non-streaming backend response to the OpenAI format.
        Default implementation is a pass-through for OpenAI-compatible backends.
        """
        return response_json
