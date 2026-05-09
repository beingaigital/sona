"""Workflow telemetry helpers."""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any, Dict, Optional


def append_ndjson_log(
    *,
    log_path: str,
    run_id: str,
    hypothesis_id: str,
    location: str,
    message: str,
    data: Optional[Dict[str, Any]] = None,
) -> None:
    """Append one NDJSON telemetry event without breaking main flow."""
    payload: Dict[str, Any] = {
        "id": f"log_{int(time.time() * 1000)}_{abs(hash((hypothesis_id, location, message))) % 10_000_000}",
        "timestamp": int(time.time() * 1000),
        "runId": run_id,
        "hypothesisId": hypothesis_id,
        "location": location,
        "message": message,
        "data": data or {},
    }
    try:
        path = Path(log_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "a", encoding="utf-8", errors="replace") as f:
            f.write(json.dumps(payload, ensure_ascii=False) + "\n")
    except Exception:
        return
