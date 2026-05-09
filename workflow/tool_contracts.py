"""Tool output contracts and validators (Day6)."""

from __future__ import annotations

from typing import Any, Dict, Iterable, Tuple


class ToolContractError(ValueError):
    """Raised when tool output violates declared schema contract."""


def normalize_extract_search_terms_time_range(data: Dict[str, Any]) -> Dict[str, Any]:
    """
    下游（event_analysis_workflow / pipeline）统一使用字符串：
    \"YYYY-MM-DD HH:MM:SS;YYYY-MM-DD HH:MM:SS\"
    模型偶发返回 {\"start\",\"end\"}，在此处规整。
    """
    out = dict(data)
    tr = out.get("timeRange")
    if isinstance(tr, dict):
        start = str(tr.get("start") or tr.get("begin") or "").strip()
        end = str(tr.get("end") or "").strip()
        out["timeRange"] = f"{start};{end}" if (start and end) else ""
    elif tr is not None and not isinstance(tr, str):
        out["timeRange"] = str(tr)
    return out


def _require_fields(data: Dict[str, Any], required: Iterable[Tuple[str, type]], *, tool_name: str) -> None:
    for key, expected_type in required:
        if key not in data:
            raise ToolContractError(f"{tool_name} 缺少必填字段: {key}")
        value = data.get(key)
        if value is not None and not isinstance(value, expected_type):
            raise ToolContractError(
                f"{tool_name} 字段类型不匹配: {key} 期望 {expected_type.__name__}，实际 {type(value).__name__}"
            )


def validate_tool_output(tool_name: str, data: Dict[str, Any]) -> Dict[str, Any]:
    """
    Validate selected hot-path tool outputs.
    字段变更触发明确失败，不做静默兼容。
    """
    if tool_name == "extract_search_terms":
        _require_fields(
            data,
            required=(
                ("eventIntroduction", str),
                ("searchWords", list),
            ),
            tool_name=tool_name,
        )
        if "timeRange" not in data:
            raise ToolContractError(f"{tool_name} 缺少必填字段: timeRange")
        tr = data.get("timeRange")
        if tr is not None and not isinstance(tr, str):
            raise ToolContractError(
                f"{tool_name} 字段类型不匹配: timeRange 期望 str（请先 normalize_extract_search_terms_time_range），实际 {type(tr).__name__}"
            )
    elif tool_name == "analysis_sentiment":
        _require_fields(
            data,
            required=(
                ("statistics", dict),
                ("positive_summary", list),
                ("negative_summary", list),
            ),
            tool_name=tool_name,
        )
        if "error" in data and data.get("error") is not None and not isinstance(data.get("error"), str):
            raise ToolContractError("analysis_sentiment 字段类型不匹配: error 必须是字符串")
    elif tool_name == "report_html":
        _require_fields(
            data,
            required=(
                ("html_file_path", str),
                ("file_url", str),
            ),
            tool_name=tool_name,
        )
    return data
