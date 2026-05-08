"""Helpers to read workflow outputs from session data (API layer)."""

from __future__ import annotations

import json
from typing import Any, Dict


def extract_report_html_path(session_data: Dict[str, Any]) -> str:
    """
    Parse the latest ``report_html`` tool message and return filesystem path.

    Mirrors the logic used by ``streamlit_app._extract_report_info``.
    """
    messages = session_data.get("messages", [])
    for msg in reversed(messages):
        if msg.get("role") != "tool":
            continue
        if msg.get("tool_name") != "report_html":
            continue
        raw = str(msg.get("content", "") or "").strip()
        if not raw:
            continue
        try:
            data = json.loads(raw)
        except Exception:
            continue
        if not isinstance(data, dict):
            continue
        html_path = str(data.get("html_file_path", "") or "").strip()
        if html_path:
            return html_path
    return ""
