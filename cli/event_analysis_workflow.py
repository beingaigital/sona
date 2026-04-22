"""舆情事件分析：CLI 薄入口；主流程编排位于 `workflow/event_analysis_pipeline`。"""

from __future__ import annotations

import json
from typing import Any, Dict, Optional

from tools import extract_search_terms, search_reference_insights
from utils.session_manager import SessionManager

from workflow.event_analysis_pipeline import _invoke_tool_to_json
from workflow.runner import run_event_analysis_workflow as _dispatch_pipeline


def run_event_analysis_workflow(
    user_query: str,
    task_id: str,
    session_manager: SessionManager,
    *,
    debug: bool = False,
    default_threshold: int = 2000,
    existing_data_path: Optional[str] = None,
    skip_data_collect: bool = False,
    force_fresh_start: Optional[bool] = None,
    report_length: Optional[str] = None,
) -> str:
    """Backward-compatible entry: delegates to workflow runner / pipeline."""
    return _dispatch_pipeline(
        user_query=user_query,
        task_id=task_id,
        session_manager=session_manager,
        debug=debug,
        default_threshold=default_threshold,
        existing_data_path=existing_data_path,
        skip_data_collect=skip_data_collect,
        force_fresh_start=force_fresh_start,
        report_length=report_length,
    )


def run_full_report_mode(
    *,
    user_query: str,
    task_id: str,
    session_manager: SessionManager,
    debug: bool = True,
    existing_data_path: Optional[str] = None,
    skip_data_collect: bool = False,
    force_fresh_start: Optional[bool] = None,
    report_length: Optional[str] = None,
) -> str:
    """完整报告模式（供 Agent full_report 等复用）。"""
    return run_event_analysis_workflow(
        user_query=user_query,
        task_id=task_id,
        session_manager=session_manager,
        debug=debug,
        existing_data_path=existing_data_path,
        skip_data_collect=skip_data_collect,
        force_fresh_start=force_fresh_start,
        report_length=report_length,
    )


def run_brief_mode(user_query: str) -> Dict[str, Any]:
    """轻量概述模式（仅提取事件简介/关键词/时间范围）。"""
    raw = extract_search_terms.invoke({"query": user_query})
    raw_text = raw if isinstance(raw, str) else str(raw)
    try:
        parsed = json.loads(raw_text)
        if isinstance(parsed, dict):
            try:
                ref = _invoke_tool_to_json(search_reference_insights, {"query": user_query, "limit": 3})
                parsed["oprag_reference"] = ref
            except Exception:
                pass
            return parsed
    except Exception:
        pass
    return {
        "eventIntroduction": "",
        "searchWords": [],
        "timeRange": "",
        "raw": raw_text,
    }
