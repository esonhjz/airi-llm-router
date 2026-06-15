import json
import datetime
import sys
from typing import Any

def log_event(event_type: str, target: str, client_ip: str, vram_percent: float, **kwargs: Any) -> None:
    """
    Outputs a highly structured JSON log directly to stdout for log aggregators to ingest.
    Must contain exactly 5 core fields + any optional context.
    """
    log_data = {
        "timestamp": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "event_type": event_type,
        "request_target": target,
        "client_ip": client_ip,
        "vram_percent": round(vram_percent, 2),
    }
    log_data.update(kwargs)
    print(json.dumps(log_data), flush=True)

def log_info(msg: str, **kwargs: Any) -> None:
    """Generic informational JSON log"""
    log_data = {
        "timestamp": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "event_type": "info",
        "message": msg,
    }
    log_data.update(kwargs)
    print(json.dumps(log_data), flush=True)

def log_error(msg: str, **kwargs: Any) -> None:
    """Generic error JSON log"""
    log_data = {
        "timestamp": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "event_type": "error",
        "message": msg,
    }
    log_data.update(kwargs)
    print(json.dumps(log_data, default=str), file=sys.stderr, flush=True)
