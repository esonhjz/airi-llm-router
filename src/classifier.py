"""Request classification engine.

Inspects the incoming payload and assigns a processing label that determines:
  1. Which queue tier the request enters (high_speed vs batch).
  2. Whether the backend adapter should be overridden (e.g. multimodal → ModelScope).

Classification rules (evaluated top to bottom, first match wins):
  MULTIMODAL  — any message contains an image_url content part.
  HEAVY       — max_tokens exceeds threshold OR prompt char count exceeds threshold.
  LIGHTWEIGHT — everything else.
"""

from __future__ import annotations

import enum
from typing import Any

from src.config import settings


class RequestLabel(enum.Enum):
    """Three-tier request classification."""
    LIGHTWEIGHT = "lightweight"   # short text → high_speed_queue
    HEAVY = "heavy"              # long text / dense generation → batch_queue
    MULTIMODAL = "multimodal"    # contains images → batch_queue + adapter override

    @property
    def queue_tier(self) -> str:
        """Maps this label to its target queue name."""
        return "high_speed" if self == RequestLabel.LIGHTWEIGHT else "batch"


def classify_request(payload: dict[str, Any]) -> RequestLabel:
    """Classifies a generic OpenAI-format payload into a processing tier.

    Evaluation order:
      1. Image scan — any image_url part in any message → MULTIMODAL.
      2. max_tokens check — exceeds configured threshold → HEAVY.
      3. Prompt length check — total chars exceed threshold → HEAVY.
      4. Fallback → LIGHTWEIGHT.
    """
    messages: list[dict] = payload.get("messages", [])

    # Rule 1: multimodal — even a single image makes it vision-class workload.
    if _has_images(messages):
        return RequestLabel.MULTIMODAL

    # Rule 2: explicit max_tokens exceeds the dense-generation threshold.
    max_tokens = payload.get("max_tokens")
    if max_tokens is not None and max_tokens >= settings.classifier_heavy_max_tokens:
        return RequestLabel.HEAVY

    # Rule 3: input prompt is extremely long — likely a summarisation/RAG task.
    if _estimate_prompt_chars(messages) >= settings.classifier_heavy_prompt_chars:
        return RequestLabel.HEAVY

    # Rule 4: short, fast, cheap.
    return RequestLabel.LIGHTWEIGHT


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _has_images(messages: list[dict]) -> bool:
    """Returns True if any message contains an image_url content part."""
    for msg in messages:
        content = msg.get("content")
        if isinstance(content, list):
            for part in content:
                if isinstance(part, dict) and part.get("type") == "image_url":
                    return True
    return False


def _estimate_prompt_chars(messages: list[dict]) -> int:
    """Sums up the character count of all text content across messages."""
    total = 0
    for msg in messages:
        content = msg.get("content", "")
        if isinstance(content, str):
            total += len(content)
        elif isinstance(content, list):
            for part in content:
                if isinstance(part, dict) and part.get("type") == "text":
                    total += len(part.get("text", ""))
    return total
