"""GPU VRAM monitoring probe.

Reads physical GPU memory utilization via NVML at a configurable interval and
exposes a global ``gpu_status()`` snapshot function that middleware and routes
can call in O(1) time without touching hardware.

Lifecycle
─────────
* ``start_monitor()`` — called once in the lifespan startup phase.
* ``stop_monitor()``  — called once in the lifespan shutdown phase.

Degradation policy
──────────────────
If NVML is unavailable (no GPU, driver mismatch, container without device
mount) or the probe loop crashes, the module automatically falls back to the
static-queue-only mode with zone locked to ``SAFE``.  This guarantees the
gateway never enters a false-positive hard-block state due to probe failure.
"""

from __future__ import annotations

import asyncio
import enum
import random
import time
from dataclasses import dataclass
from typing import Any

from src.config import settings


# ---------------------------------------------------------------------------
# Zone enum
# ---------------------------------------------------------------------------

class VramZone(enum.Enum):
    """Three-tier VRAM pressure classification."""
    SAFE = "safe"           # < threshold_warning  → full throughput
    WARNING = "warning"     # < threshold_danger   → soft throttle
    DANGER = "danger"       # ≥ threshold_danger   → hard circuit-break


# ---------------------------------------------------------------------------
# Snapshot dataclass
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class GpuSnapshot:
    """Immutable point-in-time GPU state.

    Fields are intentionally simple value types so the snapshot can be safely
    read from any coroutine without locking.
    """
    available: bool         # True if NVML was initialised successfully
    zone: VramZone
    usage_pct: float        # 0.0 – 100.0; -1.0 when unavailable
    used_mb: float          # megabytes; -1.0 when unavailable
    total_mb: float         # megabytes; -1.0 when unavailable
    consecutive_failures: int
    updated_at: float       # time.monotonic() of last successful read


_FALLBACK_SNAPSHOT = GpuSnapshot(
    available=False,
    zone=VramZone.SAFE,     # safe fallback — never hard-block without real data
    usage_pct=-1.0,
    used_mb=-1.0,
    total_mb=-1.0,
    consecutive_failures=0,
    updated_at=0.0,
)

# The global snapshot; replaced atomically on every probe tick.
_current: GpuSnapshot = _FALLBACK_SNAPSHOT

# Background task handle — stored so lifespan can cancel it cleanly.
_monitor_task: asyncio.Task | None = None


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def gpu_status() -> GpuSnapshot:
    """Returns the latest GPU snapshot.  O(1), no I/O, no lock."""
    return _current


def retry_after_hint() -> int:
    """Returns a jittered Retry-After value (seconds) for 429 responses.

    The jitter prevents a thundering-herd of clients retrying at the same
    instant once the danger zone clears.
    """
    return random.randint(
        settings.vram_retry_after_min,
        settings.vram_retry_after_max,
    )


def classify(usage_pct: float) -> VramZone:
    """Determines zone from a usage percentage."""
    if usage_pct >= settings.vram_threshold_danger:
        return VramZone.DANGER
    if usage_pct >= settings.vram_threshold_warning:
        return VramZone.WARNING
    return VramZone.SAFE


# ---------------------------------------------------------------------------
# NVML probe loop
# ---------------------------------------------------------------------------

async def _probe_loop() -> None:
    """Infinite loop that polls NVML and refreshes the global snapshot.

    Runs entirely inside an asyncio task.  NVML calls are blocking C FFI calls
    but they complete in <1 ms — fast enough to run directly in the event loop
    without offloading to a thread pool.
    """
    global _current

    try:
        import pynvml
    except ImportError:
        print(
            "[VRAM] ⚠️  pynvml not installed — GPU monitoring disabled.  "
            "Falling back to static queue thresholds."
        )
        return

    try:
        pynvml.nvmlInit()
    except pynvml.NVMLError as exc:
        print(
            f"[VRAM] ⚠️  nvmlInit() failed ({exc}) — no NVIDIA driver?  "
            "Falling back to static queue thresholds."
        )
        return

    # Grab the first GPU handle once.
    try:
        handle = pynvml.nvmlDeviceGetHandleByIndex(settings.vram_gpu_index)
        name = pynvml.nvmlDeviceGetName(handle)
        if isinstance(name, bytes):
            name = name.decode()
        print(f"[VRAM] 🎯 Monitoring GPU {settings.vram_gpu_index}: {name}")
    except pynvml.NVMLError as exc:
        print(f"[VRAM] ⚠️  Cannot access GPU {settings.vram_gpu_index}: {exc}")
        _safe_shutdown(pynvml)
        return

    failures = 0

    try:
        while True:
            try:
                info = pynvml.nvmlDeviceGetMemoryInfo(handle)
                total_mb = info.total / (1024 * 1024)
                used_mb = info.used / (1024 * 1024)
                usage_pct = (info.used / info.total) * 100.0 if info.total > 0 else 0.0
                zone = classify(usage_pct)

                _current = GpuSnapshot(
                    available=True,
                    zone=zone,
                    usage_pct=round(usage_pct, 1),
                    used_mb=round(used_mb, 1),
                    total_mb=round(total_mb, 1),
                    consecutive_failures=0,
                    updated_at=time.monotonic(),
                )
                failures = 0  # reset streak

            except pynvml.NVMLError as exc:
                failures += 1
                if failures >= settings.vram_max_probe_failures:
                    print(
                        f"[VRAM] ❌ {failures} consecutive probe failures — "
                        "degrading to static mode."
                    )
                    _current = _FALLBACK_SNAPSHOT
                else:
                    print(
                        f"[VRAM] ⚠️  Probe read failed ({exc}), "
                        f"streak {failures}/{settings.vram_max_probe_failures}"
                    )

            await asyncio.sleep(settings.vram_poll_interval)

    except asyncio.CancelledError:
        pass
    finally:
        _safe_shutdown(pynvml)


def _safe_shutdown(pynvml: Any) -> None:
    """Calls nvmlShutdown, swallowing errors to avoid crashing on exit."""
    try:
        pynvml.nvmlShutdown()
        print("[VRAM] 🛑 NVML handle released")
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Lifecycle hooks (called from main.py lifespan)
# ---------------------------------------------------------------------------

async def start_monitor() -> None:
    """Spawns the background VRAM probe task."""
    global _monitor_task
    if not settings.vram_monitor_enabled:
        print("[VRAM] Monitor disabled (vram_monitor_enabled=False)")
        return
    _monitor_task = asyncio.create_task(_probe_loop())


async def stop_monitor() -> None:
    """Cancels the probe task and waits for NVML cleanup."""
    global _monitor_task
    if _monitor_task is not None and not _monitor_task.done():
        _monitor_task.cancel()
        try:
            await _monitor_task
        except asyncio.CancelledError:
            pass
    _monitor_task = None
