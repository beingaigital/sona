"""Small helpers for consistent tool JSON shapes (optional adoption across tools)."""

from __future__ import annotations

import json
from typing import Any, Dict


def dumps_result(data: Dict[str, Any]) -> str:
    """Serialize tool return dict to JSON string (project-wide convention)."""
    return json.dumps(data, ensure_ascii=False)


def error_dict(message: str, *, result_file_path: str = "", **extra: Any) -> Dict[str, Any]:
    """Standard error payload; merge extra keys (e.g. save_path, dataset_summary)."""
    out: Dict[str, Any] = {"error": message, "result_file_path": result_file_path}
    out.update(extra)
    return out
