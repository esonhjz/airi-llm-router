from __future__ import annotations

import copy
import hashlib
import tempfile
from pathlib import Path
from typing import Any

from src.config import settings

# ---------------------------------------------------------------------------
# Internal state
# ---------------------------------------------------------------------------

_REF_PREFIX = "imgref:"

# Temp directory for offloaded image blobs
_TMPDIR = Path(tempfile.gettempdir()) / "airi_llm_router_images"

# content_hash → Path mapping for deduplication
_IMAGE_STORE: dict[str, Path] = {}

# Reference counter: how many in-flight tasks are using each image blob.
# The temp file is deleted when the count reaches zero.
_REFCOUNT: dict[str, int] = {}


def _ensure_tmpdir() -> None:
    _TMPDIR.mkdir(parents=True, exist_ok=True)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def offload_images(payload: dict[str, Any]) -> tuple[dict[str, Any], bool]:
    """
    Strips large base64 image data URLs from the request payload and writes
    them to temporary files on disk. Replaces each URL with a lightweight
    'imgref:{sha256}' reference string.

    Deduplication: identical images share a single temp file; the reference
    counter tracks how many concurrent tasks hold a reference so the file
    is only deleted when the last task releases it.

    Args:
        payload: OpenAI-format request dict (may contain multimodal content).

    Returns:
        (lightened_payload, had_offloads)
        lightened_payload  — deep copy of payload with refs substituted.
        had_offloads       — True if at least one image was offloaded.
    """
    if not settings.image_offload_enabled:
        return payload, False

    payload = copy.deepcopy(payload)
    had_offloads = False

    for message in payload.get("messages", []):
        content = message.get("content")
        if not isinstance(content, list):
            continue

        for part in content:
            if part.get("type") != "image_url":
                continue

            img_url_obj = part.get("image_url", {})
            url: str = img_url_obj.get("url", "")

            # Only offload embedded data URIs (not remote http/https URLs)
            if not url.startswith("data:image/"):
                continue

            # Skip small images — they don't pressure the queue meaningfully
            url_bytes = url.encode("utf-8")
            if len(url_bytes) < settings.image_offload_threshold:
                continue

            # SHA-256 for deduplication — two identical images → one temp file
            content_hash = hashlib.sha256(url_bytes).hexdigest()

            if content_hash not in _IMAGE_STORE:
                _ensure_tmpdir()
                tmp_path = _TMPDIR / f"{content_hash}.b64"
                tmp_path.write_bytes(url_bytes)
                _IMAGE_STORE[content_hash] = tmp_path
                _REFCOUNT[content_hash] = 0

            _REFCOUNT[content_hash] += 1
            img_url_obj["url"] = f"{_REF_PREFIX}{content_hash}"
            had_offloads = True

    return payload, had_offloads


def restore_images(payload: dict[str, Any]) -> dict[str, Any]:
    """
    Reconstructs the full payload by replacing every 'imgref:{hash}' pointer
    with the original base64 data URL read from disk.

    Safe to call multiple times (e.g. on each retry attempt) — the temp files
    remain until release_images() is called.

    Returns a deep copy; the original task payload with refs is untouched.
    """
    payload = copy.deepcopy(payload)

    for message in payload.get("messages", []):
        content = message.get("content")
        if not isinstance(content, list):
            continue

        for part in content:
            if part.get("type") != "image_url":
                continue

            img_url_obj = part.get("image_url", {})
            url: str = img_url_obj.get("url", "")

            if not url.startswith(_REF_PREFIX):
                continue

            content_hash = url[len(_REF_PREFIX):]
            if content_hash in _IMAGE_STORE:
                img_url_obj["url"] = _IMAGE_STORE[content_hash].read_bytes().decode("utf-8")

    return payload


def release_images(payload: dict[str, Any]) -> None:
    """
    Decrements reference counts for every offloaded image referenced in the
    payload. Deletes the temp file when the reference count reaches zero.

    Call this once per task in the worker's finally block, after the upstream
    request has either succeeded or exhausted all retries.
    """
    for message in payload.get("messages", []):
        content = message.get("content")
        if not isinstance(content, list):
            continue

        for part in content:
            if part.get("type") != "image_url":
                continue

            img_url_obj = part.get("image_url", {})
            url: str = img_url_obj.get("url", "")

            if not url.startswith(_REF_PREFIX):
                continue

            content_hash = url[len(_REF_PREFIX):]
            if content_hash not in _REFCOUNT:
                continue

            _REFCOUNT[content_hash] -= 1
            if _REFCOUNT[content_hash] <= 0:
                # Last reference gone — remove temp file
                try:
                    _IMAGE_STORE[content_hash].unlink(missing_ok=True)
                except Exception:
                    pass
                _IMAGE_STORE.pop(content_hash, None)
                _REFCOUNT.pop(content_hash, None)
