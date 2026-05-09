"""Workflow runner helpers (Day5 minimal extraction)."""

from __future__ import annotations

import json
import time
from typing import Any, Dict, Optional

from workflow.tool_contracts import normalize_extract_search_terms_time_range, validate_tool_output


def parse_tool_json(raw: str) -> Dict[str, Any]:
    try:
        parsed = json.loads(raw)
    except Exception as e:
        raise ValueError(f"工具返回不是合法 JSON：{str(e)}") from e
    if not isinstance(parsed, dict):
        raise ValueError("工具返回 JSON 不是对象")
    return parsed


def invoke_tool_to_json(tool_obj: Any, payload: Dict[str, Any], *, contract_name: Optional[str] = None) -> Dict[str, Any]:
    raw = tool_obj.invoke(payload)
    if not isinstance(raw, str):
        raw = str(raw)
    parsed = parse_tool_json(raw)
    if contract_name == "extract_search_terms":
        parsed = normalize_extract_search_terms_time_range(parsed)
    if contract_name:
        validate_tool_output(contract_name, parsed)
    return parsed


def invoke_tool_with_timing(
    tool_obj: Any,
    payload: Dict[str, Any],
    *,
    contract_name: Optional[str] = None,
) -> tuple[Dict[str, Any], float]:
    ts = time.time()
    result = invoke_tool_to_json(tool_obj, payload, contract_name=contract_name)
    elapsed = round(time.time() - ts, 3)
    return result, elapsed
